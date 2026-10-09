#!/usr/bin/env python3
"""auto-publish.py — волновая публикация openSUSE pilot→stable с жёстким гейтом 4/4.

Полный конвейер одного пакета:
  fresh SRPM (make-srpm.sh) -> submit в PILOT (или resume активной сборки по
  существующему build id, без дублирования) -> дождаться exact 4/4 succeeded ->
  тот же SRPM -> submit в STABLE -> снова дождаться exact 4/4 succeeded.

Жёсткость (ни один провал не может стать «успехом»):
  * failed/canceled/skipped любого obligatory chroot => пакет НЕ продвигается;
  * обязательный chroot пропущен у терминального родителя => failure;
  * таймаут наблюдения => ненулевой rc (активные сборки не дублируются:
    перезапуск наблюдает существующие build id);
  * ошибка/недоступность COPR API при чтении состояния => failure, не silence;
  * чужой chroot в билде => failure (сломанная матрица пускается в stable).

Кандидаты волны — ТОЛЬКО новые/недостроенные версии: пакет нужен в pilot, если
target-версия (state.json) не достигнута зелёным билдом в pilot. --force
(только вручную, с --confirm) разрешает пересборку уже достигнутых целей.

Использование:
  python3 bin/auto-publish.py plan \\
      --max-wave 5
  python3 bin/auto-publish.py run \\
      --max-wave 5 [--force --confirm]

Resume/продолжение после таймаута НЕ дублирует submit: активная сборка того же
target-версии перехватывается по существующему build id, по ней просто
продолжается наблюдение (см. active_build с обязательным target). Активная
сборка ДРУГОЙ версии никогда не переиспользуется — это чужой build.

Инварианты жёсткости (P0/P1, 2026-10-02):
  * единственный чистый исход пакета — `promoted` (stable exact 4/4);
    `srpm-skip`, `not-attempted`, `pilot-not-clean`, `stable-not-clean`,
    `pipeline-error` и любые прочие — FAILURE и роняют run;
  * `deferred` — НЕ успех и НЕ провал: сборка ещё не терминальна на момент
    истечения времени наблюдения. Run при этом не «зелёный» (rc=1), но и
    перезапуск не нужен: следующая волна подхватит тот же build id через
    active_build. Так COPR, который считает дольше GitHub job, не приводит ни к
    resubmit, ни к потере state/spec-гейта;
  * пакет остаётся в плане, если pilot-цель зелёная, а stable-цель ещё не
    зелёная (failed/missing/active) — иначе stable-гэпы никогда не закрываются;
  * если pilot-цель уже зелёная, в stable уходит ТОТ ЖЕ SRPM из pilot-сборки
    (скачивается по URL пилота), а не свежесобранный: иначе цепочка
    «одинаковый SRPM pilot→stable» рвётся;
  * make-srpm.sh вызывается с точным target из плана, а версия готового SRPM
    проверяется ДО submit;
  * уже успешная stable-цель не перестраивается (кроме явного --force).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

from copr_published import (  # repodata = источник правды о публикации
    PublishProbeError,
    chroot_presence,
    completed_table,
    fetch_published_per_chroot,
    has_newer,
    is_complete,
    target_published as repodata_has_target,
)
from copr_waiter import (  # переиспользуемый state machine (тот же, что в CI)
    ApiError,
    fetch_chroot_states,
    fetch_parent_state,
    parse_build_ids,
    wait_for_build,
)

API_BASE = "https://copr.fedorainfracloud.org"
OWNER = "arcticlore"
PILOT = os.environ.get("COPR_PILOT", "arcticlore/candy-opensuse-pilot")
STABLE = os.environ.get("COPR_STABLE", "arcticlore/candy-opensuse")
ACTIVE = {"pending", "starting", "importing", "running", "queued", "waiting"}
FINISHED_OK = {"succeeded"}

# Единственный исход, при котором пакет считается успешно проведённым волной.
SUCCESS_OUTCOMES = frozenset({"promoted"})
# Исходы, которые раньше ошибочно считались «не помешали run» (P0).
NEVER_SUCCESS_OUTCOMES = frozenset({"srpm-skip", "not-attempted"})
DOWNLOAD_BASE = os.environ.get(
    "COPR_DOWNLOAD_BASE", "https://download.copr.fedorainfracloud.org/results")


class WaveError(Exception):
    """Ошибка конвейера: API недоступен, битый ввод/вывод, запрещённая операция."""


class WavePlanError(WaveError):
    pass


class RecoveryAllowlistError(WaveError):
    """Recovery-режим включён без валидного allowlist — fail-closed."""


def load_retry_brake():
    """Подгрузить bin/retry_brake.py по пути (модуль с подчёркиванием)."""
    import importlib.util
    bin_dir = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "retry_brake", bin_dir / "retry_brake.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_allowlist(value, pkgs: list[dict], recovery: bool) -> list[str] | None:
    """Разобрать recovery-allowlist. Fail-closed при пустом/битом вводе.

    Значение — либо путь к файлу (по строке на имя), либо CSV-список имён.
    Возвращает отсортированный дедуплицированный список имён из pkgs.json
    либо None, если recovery/allowlist не заданы.
    """
    if not value:
        if recovery:
            raise RecoveryAllowlistError(
                "recovery-режим включён, но allowlist пуст — волна запрещена")
        return None
    raw = str(value).strip()
    candidates = []
    p = Path(raw)
    if os.path.exists(raw) and p.is_file():
        text = p.read_text(encoding="utf-8")
        candidates = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    else:
        candidates = [c.strip() for c in raw.split(",")]
    names = sorted({c for c in candidates if c})
    if not names:
        raise RecoveryAllowlistError(
            "recovery-allowlist не содержит имён — волна запрещена")
    known = {pkg["name"] for pkg in pkgs}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise RecoveryAllowlistError(
            f"recovery-allowlist ссылается на неизвестные пакеты: {unknown}")
    return names


def apply_allowlist(pkgs: list[dict], names: list[str] | None) -> list[dict]:
    """Отфильтровать пакеты ДО построения плана (план не перерастёт allowlist)."""
    if names is None:
        return pkgs
    allowed = set(names)
    return [pkg for pkg in pkgs if pkg["name"] in allowed]


def assert_plan_within_allowlist(plan: list[dict], names: list[str] | None) -> None:
    """Инвариант: ни одна запись плана не выходит за allowlist."""
    if names is None:
        return
    allowed = set(names)
    outside = sorted({p["name"] for p in plan if p["name"] not in allowed})
    if outside:
        raise RecoveryAllowlistError(
            f"план вышел за recovery-allowlist: {outside}")


def preflight_gate(preflight_path) -> tuple[bool, str]:
    """Прочитать preflight-evidence. Нет файла / ok!=true => submit запрещён."""
    if not preflight_path:
        return True, "preflight не задан (обычная волна)"
    p = Path(preflight_path)
    if not p.exists():
        return False, f"preflight-evidence отсутствует: {preflight_path}"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        return False, f"preflight-evidence битый JSON: {exc}"
    if not data.get("ok"):
        return False, "preflight красный: " + "; ".join(data.get("reasons", [])[:3])
    return True, "preflight зелёный"


def api_project(project: str) -> tuple[str, str]:
    if "/" in project:
        return project.split("/", 1)
    return OWNER, project


def fetch_builds(owner: str, name: str, token: str | None) -> list[dict]:
    """Все билды проекта (полная пагинация). ApiError если API недоступен."""
    builds: list[dict] = []
    offset = 0
    while True:
        url = (f"{API_BASE}/api_3/build/list?ownername={owner}"
               f"&projectname={name}&limit=100&offset={offset}")
        import urllib.request
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # network / HTTP / JSON
            raise WaveError(f"COPR API недоступен для {owner}/{name}: {exc}") from exc
        items = data.get("items")
        if not isinstance(items, list):
            raise WaveError(f"build/list ({owner}/{name}) без 'items': {data!r:.200}")
        builds.extend(items)
        if len(items) < 100:
            return builds
        offset += 100
        time.sleep(0.4)


def history(builds: list[dict]) -> dict[str, list[tuple[str, str, int]]]:
    """{name: [(state, version, build_id)]} — новые первыми."""
    out: dict[str, list[tuple[str, str, int]]] = {}
    for b in sorted(builds, key=lambda x: x.get("id", 0), reverse=True):
        sp = b.get("source_package") or {}
        name = sp.get("name", "")
        if not name:
            continue
        out.setdefault(name, []).append((b.get("state", ""), sp.get("version", ""), b.get("id", 0)))
    return out


def latest_succeeded_ver(hist: list[tuple[str, str, int]]) -> str | None:
    for state, ver, _bid in hist:
        if state == "succeeded":
            return ver
    return None


def ver_base(ver: str) -> str:
    """Базовая версия: '1.2.3-4.fc44' -> '1.2.3' (COPR отдаёт ver-release)."""
    return ver.split("-", 1)[0] if "-" in ver else ver


def target_reached(target: str, hist: list[tuple[str, str, int]]) -> bool:
    """Цель достигнута зелёной сборкой ровно этой базовой версии."""
    if not hist or not target:
        return False
    for state, ver, _bid in hist:
        if state == "succeeded" and ver_base(ver) == ver_base(target):
            return True
    return False


def needs_pilot(name: str, target: str, hist: list[tuple[str, str, int]]) -> bool:
    """Пакет нужен в pilot, если цель не достигнута зелёным (или никогда не собирался)."""
    return not target_reached(target, hist)


def active_build(hist: list[tuple[str, str, int]], target: str | None = None) -> int | None:
    """Самый свежий НЕтерминальный билд пакета.

    При заданном target возвращается только build той же базовой версии:
    resume чужой версии = подмена проверяемого артефакта (P0).
    """
    for state, ver, bid in hist:
        if state in ACTIVE:
            if target is None or ver_base(ver) == ver_base(target):
                return bid
    return None


# Provenance точного pilot-SRPM для терминальных 3/4-пакетов.
#
# Эти сборки уже опубликовали успешные chroot'ы, поэтому их артефакт и есть
# источник истины для remediation. Искать «любую succeeded-сборку той же
# версии» нельзя: у терминального parent'а state != succeeded, а более старая
# зелёная сборка той же базовой версии несла бы ДРУГОЙ SRPM — в репозитории
# оказались бы два артефакта на один target.
#
# Ключ — ТОЧНАЯ пара (имя, нормализованный target). Пин по одному имени
# «протух» бы на следующей версии и отдавал бы старый SRPM новому target'у.
PILOT_SRPM_PROVENANCE = {
    ("dua", "2.45.1"): 11063545,
    ("dysk", "3.7.1"): 11064066,
    ("ghfetch", "20261002.4b44a4f"): 11064212,
    ("pokemon-icat", "20261002.54d4bc5"): 11064380,
}


def pilot_srpm_build_for(name: str, target: str,
                         hist: list[tuple[str, str, int]],
                         active: int | None) -> int | None:
    """Точный id pilot-сборки-ИСТОЧНИКА SRPM для target (отдельно от resume).

    `pilot_build` отвечает «за какую сборку продолжать наблюдение» — это
    НЕтерминальная активная сборка, и для терминального 3/4 она равна None.
    `pilot_srpm_build` отвечает «чей SRPM переиспользуем» — и для 3/4 это
    ровно та сборка, которой уже опубликованы успешные chroot'ы.

    Порядок:
      1. точный пин по паре (имя, нормализованный target) — авторитетен;
      2. активная сборка ИМЕННО этого target;
      3. ДОКАЗАННАЯ succeeded-сборка этого target (обычный promote-only,
         где pilot уже 4/4 и активной сборки нет);
      4. иначе None — fetch уйдёт в fail-closed.
    Новейшая сборка target'а, независимо от state, НЕ выбирается: terminal
    failed parent не доказательство provenance и не должен подменяться.
    """
    pinned = PILOT_SRPM_PROVENANCE.get((name, ver_base(target)))
    if pinned is not None:
        return pinned
    if active is not None:
        return active
    for state, ver, bid in hist:
        if state == "succeeded" and ver_base(ver) == ver_base(target):
            return bid
    return None


def recovery_pinned_target(name: str) -> str | None:
    """Target из provenance-пина для recovery — единственный пин на имя.

    Пин привязан к ТОЧНОЙ паре (имя, version). В recovery-режиме цель обязана
    быть именно этой версией: иначе дневной refresh (commit-fallback date.hash)
    сдвинул бы target, пин перестал бы совпадать и remediation собрала бы не тот
    артефакт. Возвращает None, если для имени нет ровно одного пина.
    """
    vers = sorted({ver for (n, ver) in PILOT_SRPM_PROVENANCE if n == name})
    return vers[0] if len(vers) == 1 else None


def apply_recovery_pins(versions: dict[str, str],
                        allow_names: list[str] | None) -> dict[str, str]:
    """В recovery-режиме взять target из provenance-пина, а не из refresh дня."""
    if not allow_names:
        return versions
    out = dict(versions)
    for name in allow_names:
        pin = recovery_pinned_target(name)
        if pin is None:
            continue
        if out.get(name) and out[name] != pin:
            print(f"  recovery-pin: {name} target {out[name]} -> {pin} "
                  f"(точный provenance, не дневной refresh)")
        out[name] = pin
    return out


def is_prioritizable(pkg: dict) -> bool:
    return str(pkg.get("enabled", "true")).lower() not in ("false", "0")


def prioritize(pkgs: list[dict]):
    """Сортировка кандидатов: prio (ниже = раньше), затем имя — детерминированно."""
    return sorted(pkgs, key=lambda p: (p.get("prio", 5), p["name"]))


def probe_all(project: str, chroots: list[str]) -> dict:
    """Один проход по repodata проекта: union, per-chroot и intersection.

    Раньше union и intersection читались независимо (по четыре HTTP-запроса
    каждый), хотя intersection — это пересечение тех же самых per-chroot
    таблиц. Один проход убирает половину запросов и, главное, исключает
    рассинхрон между двумя чтениями: union и intersection теперь всегда
    описывают ОДИН момент времени.

    Fail closed: если реподата недоступна или не разобралась, состояние
    репозитория НЕИЗВЕСТНО, а при неизвестном состоянии любая сборка рискует
    оказаться дубликатом уже опубликованного пакета.
    """
    owner, name = api_project(project)
    try:
        per = fetch_published_per_chroot(name, chroots, owner=owner)
    except PublishProbeError as exc:
        raise WavePlanError(f"repodata {project} недоступна: {exc}") from exc
    union: dict[str, set[str]] = {}
    for table in per.values():
        for pkg, versions in table.items():
            union.setdefault(pkg, set()).update(versions)
    return {"union": union, "per_chroot": per, "completed": completed_table(per)}


def probe_published(project: str, chroots: list[str]) -> dict[str, set[str]]:
    """Union по всем chroot: годится для анти-даунгрейда, НЕ для «цель достигнута»."""
    return probe_all(project, chroots)["union"]


def probe_completed(project: str, chroots: list[str]) -> dict[str, set[str]]:
    """Версии, опубликованные ВО ВСЕХ chroot (пересечение repodata)."""
    return probe_all(project, chroots)["completed"]


def target_is_stale(published: dict[str, set[str]] | None, name: str, target: str) -> bool:
    """В проекте уже версия НОВЕЕ цели волны — promote был бы даунгрейдом."""
    return has_newer(published, name, target)


def stale_targets(pkgs: list[dict], versions: dict[str, str],
                  pilot_published: dict[str, set[str]] | None,
                  stable_published: dict[str, set[str]] | None) -> list[dict]:
    """Пакеты с устаревшей целью в state.json — их нельзя ни пересобирать, ни промоутить."""
    out: list[dict] = []
    for p in pkgs:
        name = p["name"]
        target = versions.get(name, "")
        if not target or not is_prioritizable(p):
            continue
        for project, table in (("pilot", pilot_published), ("stable", stable_published)):
            if target_is_stale(table, name, target):
                out.append({"name": name, "target": target, "project": project})
    return out


def build_plan(
    pkgs: list[dict],
    versions: dict[str, str],
    pilot_hist: dict[str, list[tuple[str, str, int]]],
    max_wave: int,
    force: bool = False,
    stable_hist: dict[str, list[tuple[str, str, int]]] | None = None,
    pilot_published: dict[str, set[str]] | None = None,
    stable_published: dict[str, set[str]] | None = None,
    pilot_completed: dict[str, set[str]] | None = None,
    stable_completed: dict[str, set[str]] | None = None,
) -> list[dict]:
    """Кандидаты волны.

    «Достигнута цель» = версия опубликована ВО ВСЕХ chroot по repodata
    (pilot_completed / stable_completed) и НИЧЕГО больше. Прежде здесь стояло
    `target_reached(target, hist) or repodata_has_target(...)`, то есть ИЛИ с
    build-list COPR: зелёная запись в build-list засчитывалась как «цель
    достигнута». Это неверно по двум причинам. Первая — build-list ничего не
    говорит о том, в каких chroot версия появилась, и общий parent build может
    быть зелёным при провале одного chroot. Вторая — записи об успешных
    сборках в COPR неполны и нестабильны во времени (у 133 из 137
    опубликованных пакетов записи `succeeded` нет вовсе), поэтому история
    сборок не может быть основанием закрытия цели. Решение о завершении
    принимает ТОЛЬКО пересечение repodata; build-list используется только для
    выбора build id для продолжения (active_build), то есть для resume, а не
    для вердикта.
    """

    if max_wave < 1:
        raise WavePlanError("max_wave должен быть >= 1")
    stable_hist = stable_hist or {}
    plan: list[dict] = []
    for p in prioritize(pkgs):
        name = p["name"]
        target = versions.get(name, "")
        if not target:
            continue
        if not is_prioritizable(p):
            continue
        phist = pilot_hist.get(name, [])
        shist = stable_hist.get(name, [])
        if target_is_stale(pilot_published, name, target) or \
                target_is_stale(stable_published, name, target):
            continue  # цель устарела — пересборка только испортила бы репозиторий
        pilot_ok = repodata_has_target(pilot_completed, name, target)
        stable_ok = repodata_has_target(stable_completed, name, target)
        if not force and pilot_ok and stable_ok:
            continue  # обе цели зелёные — ничего не делаем
        active = None if pilot_ok else active_build(phist, target)
        plan.append({
            "name": name,
            "target": target,
            # pilot-сборку продолжаем только если версия совпадает с целью
            "pilot_build": active,
            "stable_build": None if stable_ok else active_build(shist, target),
            # provenance SRPM — ОТДЕЛЬНОЕ поле: для терминального 3/4 active
            # равен None, но источник опубликованных chroot'ов известен.
            "pilot_srpm_build": pilot_srpm_build_for(name, target, phist, active),
            "pilot_ok": pilot_ok,
            "stable_ok": stable_ok,
            "mode": "promote-only" if pilot_ok else "full",
            "force": force,
        })
        if len(plan) >= max_wave:
            break
    return plan


def wait_exact_4_4(build_id: int, chroots: list[str], token: str | None,
                   timeout_min: float, interval: float) -> tuple[int, dict, str]:
    """Дождаться terminal-исхода одной сборки через copr_waiter.

    Возвращает (rc, {chroot: state}, verdict). rc==0 означает ровно 4/4 succeeded.
    """
    def fetch(bid):
        return fetch_chroot_states(bid, API_BASE, token)
    def fetch_parent(bid):
        return fetch_parent_state(bid, API_BASE, token)
    def step(n):
        return min(interval * (2 ** (n - 1)), 600.0)
    def progress(n, state):
        print(f"  [poll {n}] {json.dumps(state, sort_keys=True)}", flush=True)
    try:
        return wait_for_build(build_id, chroots, fetch, timeout_min * 60.0, step,
                              progress, fetch_parent)
    except ApiError as exc:
        print(f"  [API-ERROR] build {build_id}: {exc}", flush=True)
        return 1, {}, f"api error: {exc}"


def build_still_active(build_id: int, token: str | None = None) -> bool:
    """Сборка ещё не терминальна.

    Нужна, чтобы отличить «зеркало/COPPR валит билд» от «мы не дождались».
    Первое — провал пакета, второе — deferred: COPR ещё считает, и следующая
    волна продолжит наблюдение по тому же build id, без resubmit.
    """
    try:
        state = fetch_parent_state(int(build_id), API_BASE, token)
    except Exception:  # noqa: BLE001 — неизвестно => считаем терминальным (fail-closed)
        return False
    return str(state or "").lower() in ACTIVE


def srpm_base_version(path: str, name: str | None = None) -> str:
    """Базовая версия готового SRPM (rpm -qp, иначе — разбор имени файла).

    Fallback по имени файла требует name: без него 'tetro-tui-3.6.2-1.fc44'
    неоднозначен (имя пакета само содержит дефис).
    """
    try:
        r = subprocess.run(["rpm", "-qp", "--qf", "%{VERSION}\n", path],
                           capture_output=True, text=True, timeout=120)
        got = (r.stdout or "").strip()
        if r.returncode == 0 and got:
            return ver_base(got)
    except Exception:  # noqa: BLE001 — rpm может отсутствовать, это не фатально
        pass
    fname = os.path.basename(path)
    stem = fname[:-len(".src.rpm")] if fname.endswith(".src.rpm") else fname
    if name and stem.startswith(name + "-"):
        # остаток — ровно "<version>-<release>"
        return ver_base(stem[len(name) + 1:])
    rest = stem.split("-", 1)[1] if "-" in stem else stem
    return ver_base(rest)


def sha256_file(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_srpm(name: str, target: str, log_dir: Path) -> str:
    """Свежий SRPM ровно для target: make-srpm.sh NAME TARGET.

    raise WaveError при любом провале (включая несовпадение версии готового
    SRPM с целью плана) — версия проверяется ДО submit. Возвращает '' только
    если make-srpm честно пометил SKIP; такой исход = FAILURE пакета.
    """
    for f in log_dir.parent.glob(f"SRPMS/{name}-*.src.rpm"):
        f.unlink()
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"srpm-{name}.log"
    rc = subprocess.run(
        ["bin/make-srpm.sh", name, target],
        cwd=log_dir.parent, stdout=open(log, "w"), stderr=subprocess.STDOUT,
    ).returncode
    if rc != 0:
        tail = log.read_text(errors="replace").splitlines()[-12:]
        raise WaveError(f"make-srpm {name} rc={rc}:\n  " + "\n  ".join(tail))
    srpms = sorted(log_dir.parent.glob(f"SRPMS/{name}-*.src.rpm"))
    if not srpms:
        if "[SKIP]" in log.read_text(errors="replace"):
            return ""
        raise WaveError(f"SRPM {name} не найден и не SKIP")
    srpm = str(srpms[0])
    got = srpm_base_version(srpm, name)
    if got != ver_base(target):
        raise WaveError(
            f"SRPM {name} версии {got} != цель плана {ver_base(target)} "
            f"(SRPM {os.path.basename(srpm)}) — submit заблокирован")
    return srpm


def fetch_pilot_srpm(name: str, target: str, builds: list[dict],
                     dest_dir: Path, build_id: int | None = None) -> tuple[str, str, int]:
    """Скачать ТОТ ЖЕ SRPM pilot-сборки. -> (path, sha256, build_id).

    Гарантия «одинаковый SRPM»: если часть chroot'ов уже опубликована (3/4) либо
    план подхватил существующую сборку, свежий make_srpm() дал бы другой артефакт —
    и в репозитории оказались бы два разных SRPM на один target. Поэтому здесь
    берётся байт-в-байт тот же SRPM, который уже уходил в pilot.

    build_id — ТОЧНЫЙ id pilot_srpm_build из immutable-плана. Fetch идёт
    строго по нему: отсутствие сборки, чужая пакет-принадлежность или другая
    базовая версия — это fail-closed, а не повод искать «любую succeeded
    сборку той же версии». Такая подмена дала бы в репозитории второй SRPM
    на один target.
    """
    if build_id is None:
        raise WaveError(
            f"{name}: provenance SRPM (pilot_srpm_build) не задан для {target} — "
            "искать succeeded-сборку той же версии запрещено, remediation без "
            "byte-идентичности невозможна")
    build_id = int(build_id)
    found = None
    for b in builds:
        if b.get("id") != build_id:
            continue
        sp = b.get("source_package") or {}
        url = sp.get("url")
        sp_name = sp.get("name")
        if sp_name and sp_name != name:
            raise WaveError(
                f"{name}: сборка {build_id} принадлежит {sp_name}, а не {name} — "
                "provenance не совпадает, submit заблокирован")
        if ver_base(sp.get("version") or "") != ver_base(target):
            raise WaveError(
                f"{name}: сборка {build_id} несёт версию {sp.get('version')!r}, "
                f"а цель {target!r} — provenance не совпадает")
        if not url:
            raise WaveError(
                f"{name}: pilot-сборка {build_id} есть, но без SRPM URL — "
                "точный артефакт недоступен, submit невозможен")
        found = (build_id, url)
        break
    if found is None:
        raise WaveError(
            f"{name}: pilot-сборка {build_id} не найдена в истории проекта — "
            "точный source build недоступен, remediation невозможна")
    build_id, url = found
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = os.path.basename(url)
    dest = dest_dir / fname
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=900) as resp, open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
    except Exception as exc:  # noqa: BLE001
        raise WaveError(f"{name}: не скачался pilot SRPM {url}: {exc}") from exc
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise WaveError(f"{name}: pilot SRPM пустой ({url})")
    tmp.replace(dest)
    got = srpm_base_version(str(dest), name)
    if got != ver_base(target):
        raise WaveError(f"{name}: скачанный SRPM версии {got} != цель {ver_base(target)}")
    return str(dest), sha256_file(str(dest)), build_id


def submit(project: str, srpm: str, copr_conf: str | None,
           chroots: list[str] | None = None) -> str:
    """Submit SRPM (copr-cli --nowait). Возвращает числовой build id.

    ``chroots`` ограничивает сборку перечисленными chroot'ами (-r). Это
    обязательно для повторной попытки: если 3 из 4 chroot уже опубликованы,
    повторный submit во все четыре пересобирает и заново публикует успешные —
    и, главное, роняет уже зелёные chroot тем же самым зеркальным багом,
    который их уже валил. Пересобирать можно только то, что не опубликовано.
    """
    cmd = ["copr-cli"]
    if copr_conf:
        cmd += ["--config", copr_conf]
    cmd += ["build", "--nowait", project, srpm]
    if chroots:
        for chroot in chroots:
            cmd += ["-r", chroot]
    env = dict(os.environ)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
    text = r.stdout or ""
    if r.returncode != 0:
        raise WaveError(f"copr-cli submit {project} rc={r.returncode}: {(r.stderr or '')[:300]}")
    ids = parse_build_ids(text)
    if len(ids) != 1:
        raise WaveError(
            f"ожидался ровно 1 build id (submit {project}), получено {ids}: {text[:300]}")
    return str(ids[0])


def missing_chroots(project: str, chroots: list[str], name: str, target: str,
                    per_chroot: dict | None = None) -> list[str]:
    """Chroot'ы, где target-версия ещё НЕ опубликована.

    Если per_chroot не передан, читаем repodata сами — так process_package
    остаётся корректным и без предварительного probe (и в тестах, и при
    ручном запуске на одном пакете).
    """
    per = per_chroot if per_chroot is not None else probe_all(project, chroots)["per_chroot"]
    present = chroot_presence(per, name, target)
    return [c for c in chroots if c not in present]


def verify_published(project: str, chroots: list[str], name: str, target: str,
                     propagate_min: float = 0.0,
                     interval: float = 60.0) -> tuple[bool, dict]:
    """Опубликована ли target-версия ВО ВСЕХ chroot — единственный вердикт.

    COPR помечает сборку succeeded раньше, чем новая repodata разъезжается
    по CDN, и мгновенная проверка дала бы ложный «не опубликовано» на только
    что зелёной сборке, поэтому при ``propagate_min`` чтение повторяется.
    """
    deadline = time.monotonic() + max(0.0, propagate_min) * 60.0
    while True:
        per = probe_all(project, chroots)["per_chroot"]
        if is_complete(per, name, target):
            return True, per
        if time.monotonic() >= deadline:
            return False, per
        print(f"  [repodata] {name} {target}: ждём разъезд CDN "
              f"({len(chroot_presence(per, name, target))}/{len(chroots)})", flush=True)
        time.sleep(interval)


def await_stage(project: str, chroots: list[str], name: str, target: str,
                bid: str, stage: str, token: str | None, deadline: float,
                propagate_cap: float, interval: float, wait_chroots: list[str],
                out: dict) -> tuple[str, dict]:
    """Дождаться сборки и вынести вердикт ТОЛЬКО по пересечению repodata.

    Возвращает ('complete' | 'deferred' | 'failed', per_chroot).

    Раньше вердикт давал rc из wait_for_build, то есть зелёный parent build,
    а aggregated repodata после сборки не перечитывалось вовсе. Для сборки,
    поданной с -r на один chroot, такой вердикт заведомо неверен: одна
    зелёная сборка одного chroot никогда не бывает «4/4», хотя repodata уже
    показывает полную публикацию — остальные три chroot закрыла предыдущая
    волна. Поэтому rc теперь только диагностика, а решение принимает
    пересечение repodata по всем chroot проекта.
    """
    # Бюджет ОДИН на весь пакет: pilot и stable делят его, иначе два stage по
    # timeout_min давали бы суммарно вдвое против job-таймаута и job убивался
    # бы GitHub'ом посреди наблюдения — тогда deferred не успел бы записаться.
    remain_min = max(0.0, (deadline - time.monotonic()) / 60.0)
    rc, seen, verdict = wait_exact_4_4(int(bid), wait_chroots, token, remain_min, interval)
    out[f"{stage}_rc"] = rc
    out[f"{stage}_verdict"] = verdict
    out[f"{stage}_chroots"] = {c: seen.get(c, "missing") for c in wait_chroots}

    # Ожидание разъезда CDN тоже живёт в пределах того же бюджета.
    propagate_min = min(propagate_cap, max(0.0, (deadline - time.monotonic()) / 60.0))
    complete, per = verify_published(project, chroots, name, target, propagate_min, interval)
    published = sorted(chroot_presence(per, name, target))
    absent = [c for c in chroots if c not in published]
    out[f"{stage}_published_chroots"] = published
    out[f"{stage}_still_missing"] = absent
    if complete:
        return "complete", per
    if build_still_active(int(bid), token):
        # COPR ещё считает: это НЕ провал пакета и НЕ успех. Исход deferred —
        # следующая волна продолжит наблюдение по этому же build id; resubmit
        # не делается (build_plan подхватит активную сборку через active_build).
        out["outcome"] = "deferred"
        out["deferred_stage"] = stage
        out["error"] = (f"{name}: {project} build {bid} ещё активен ({verdict}); "
                        f"опубликовано {len(published)}/{len(chroots)} — deferred, "
                        f"не resubmit")
        return "deferred", per
    out["outcome"] = f"{stage}-not-clean"
    out["error"] = (f"{name}: {project} build {bid} терминален ({verdict}), но "
                    f"опубликовано только {len(published)}/{len(chroots)} — нет: "
                    f"{', '.join(absent) if absent else '?'}")
    return "failed", per


def process_package(
    pkg: dict,
    chroots: list[str],
    token: str | None,
    copr_conf: str | None,
    log_dir: Path,
    timeout_min: float,
    stable_hist: dict[str, list[tuple[str, str, int]]],
    pilot_builds: list[dict],
    pilot_published: dict[str, set[str]] | None = None,
    stable_published: dict[str, set[str]] | None = None,
    pilot_per_chroot: dict | None = None,
    stable_per_chroot: dict | None = None,
    propagate_min: float = 0.0,
) -> dict:
    name = pkg["name"]
    target = pkg["target"]
    out = dict(pkg)
    out.setdefault("mode", "full")
    # Один бюджет на весь пакет (см. await_stage): суммарное наблюдение плюс
    # ожидание CDN никогда не превысит timeout_min, а значит job-таймаут всегда
    # оставляет запас на запись отчёта, deferred-вердикт и upload артефактов.
    deadline = time.monotonic() + timeout_min * 60.0

    # Страховка от TOCTOU: план мог быть построен до того, как в репозитории
    # появилась более новая версия. Промоутить старую нельзя ни при каком
    # --force, поэтому исход — провал волны, а не 'promoted'. Union-таблица
    # здесь уместна: «есть ли где-то новее» — это ровно вопрос про любой chroot.
    for project, table in ((PILOT, pilot_published), (STABLE, stable_published)):
        if target_is_stale(table, name, target):
            out["outcome"] = "downgrade-blocked"
            out["error"] = (f"{name}: в {project} уже версия новее цели {target} "
                            "— сборка/промоут отменены (обновите state.json)")
            return out

    # Provenance — отдельное поле immutable-плана, а не pilot_build: для
    # терминального 3/4 active-сборки нет, но источник опубликованных chroot'ов
    # известен точным id.
    out["pilot_srpm_build"] = pkg.get("pilot_srpm_build")

    # --- PILOT scope: сначала узнаём, чего именно не хватает. От этого зависит
    #     и выбор SRPM, и то, куда вообще можно submit.
    if out["mode"] == "promote-only":
        pilot_missing: list[str] = []
    else:
        pilot_missing = missing_chroots(PILOT, chroots, name, target, pilot_per_chroot)
    out["pilot_missing_chroots"] = pilot_missing

    # Частичная публикация (3/4) и подхваченная планом сборка (resume) означают,
    # что target УЖЕ уходил в pilot. make_srpm() в этих случаях запрещён:
    # он собрал бы другой артефакт, и репозиторий получил бы два разных SRPM
    # на одну и ту же версию — нарушение exact-SRPM.
    resumed = pkg.get("pilot_build") is not None
    already_in_pilot = len(pilot_missing) < len(chroots)
    reuse_exact_srpm = resumed or already_in_pilot

    # --- SRPM: точный артефакт pilot-сборки либо свежая сборка только когда
    #     target в pilot ещё не появлялся. Версия проверяется ДО submit.
    if reuse_exact_srpm:
        try:
            srpm, sha, src_build = fetch_pilot_srpm(
                name, target, pilot_builds, log_dir.parent / "SRPMS",
                build_id=pkg.get("pilot_srpm_build"))
        except WaveError as exc:
            out["outcome"] = "not-attempted"
            out["error"] = str(exc)
            return out
        out["srpm_source"] = f"pilot-build:{src_build}"
    else:
        srpm = make_srpm(name, target, log_dir)
        if not srpm:
            # Раньше srpm-skip НЕ ронял run — это был P0 fail-open.
            out["outcome"] = "srpm-skip"
            out["error"] = (f"make-srpm.sh сообщил SKIP для {name}@{target}; "
                            "цель не собрана — считается провалом")
            return out
        sha = sha256_file(srpm)
        out["srpm_source"] = "fresh-make-srpm"
    out["srpm"] = os.path.basename(srpm)
    out["srpm_sha256"] = sha

    # --- PILOT stage: пересобираем ТОЛЬКО то, что не опубликовано ---
    if out["mode"] == "promote-only":
        out["pilot_ok"] = True
    elif not pilot_missing:
        # 4/4 в repodata: сборка этого target уже была и закрыта.
        out["pilot_ok"] = True
    else:
        pilot_bid = pkg.get("pilot_build")
        if pilot_bid is None:
            try:
                pilot_bid = submit(PILOT, srpm, copr_conf, chroots=pilot_missing)
                out["pilot_submitted"] = True
                out["pilot_submit_chroots"] = pilot_missing
                wait_chroots = pilot_missing
            except WaveError as exc:
                out["outcome"] = "pilot-submit-failure"
                out["error"] = str(exc)
                return out
        else:
            # Продолжаем наблюдение за существующей сборкой: она создана
            # на весь проект, ждём её по всем chroot, resubmit не делаем.
            out["pilot_resumed"] = True
            wait_chroots = chroots
        out["pilot_build"] = pilot_bid
        status, pilot_per_chroot = await_stage(
            PILOT, chroots, name, target, str(pilot_bid), "pilot", token,
            deadline, propagate_min, 30.0, wait_chroots, out)
        if status != "complete":
            return out
        out["pilot_ok"] = True

    # --- STABLE stage (тот же SRPM, тоже только недостающие chroot) ---
    stable_missing = missing_chroots(STABLE, chroots, name, target, stable_per_chroot)
    out["stable_missing_chroots"] = stable_missing
    if not stable_missing:
        # 4/4 в stable repodata — promote уже состоялся в прошлой волне.
        out["stable_ok"] = True
        out["outcome"] = "promoted"
        return out
    stable_bid = pkg.get("stable_build")
    if stable_bid is None:
        try:
            stable_bid = submit(STABLE, srpm, copr_conf, chroots=stable_missing)
            out["stable_submitted"] = True
            out["stable_submit_chroots"] = stable_missing
            wait_chroots = stable_missing
        except WaveError as exc:
            out["outcome"] = "stable-submit-failure"
            out["error"] = str(exc)
            return out
    else:
        out["stable_resumed"] = True
        wait_chroots = chroots
    out["stable_build"] = stable_bid
    status2, _ = await_stage(
        STABLE, chroots, name, target, str(stable_bid), "stable", token,
        deadline, propagate_min, 30.0, wait_chroots, out)
    if status2 == "complete":
        out["stable_ok"] = True
        out["outcome"] = "promoted"
    return out


def deferred_items(items: list[dict]) -> list[dict]:
    """Пакеты, которые не провалились, а просто не дождались терминала COPR.

    Их нельзя считать успехом (см. SUCCESS_OUTCOMES), но и нельзя чинить
    перезапуском: следующая волна продолжит наблюдение по тем же build id.
    """
    return [e for e in items if e.get("outcome") == "deferred"]


def failed_items(items: list[dict]) -> list[dict]:
    """Всё, что не является exact-4/4 промоушеном, — FAILURE (в т.ч. srpm-skip)."""
    return [e for e in items if e.get("outcome") not in SUCCESS_OUTCOMES]


def wave_is_clean(items: list[dict]) -> bool:
    """Чистая волна == каждый пакет promoted. Пустая волна чиста по определению."""
    return not failed_items(items)


def collect_errors(items: list[dict]) -> list[str]:
    """Все не-чистые исходы видны в отчёте — включая srpm-skip/pipeline-error."""
    return [f"{e.get('name', '?')}: {e.get('outcome', 'unknown')}"
            + (f" — {e['error']}" if e.get("error") else "")
            for e in failed_items(items)]


def summarize(outcomes: list[dict]) -> dict:
    return dict(Counter(o.get("outcome", "unknown") for o in outcomes))


def render_markdown(wave: dict) -> str:
    lines = [
        "### Волна pilot→stable (auto-publish)",
        "",
        f"Проекты: pilot `{PILOT}`, stable `{STABLE}`",
        f"`fetched_at`={wave.get('fetched_at')}  `$GITHUB_RUN_NUMBER`",
        "",
        "| пакет | target | pilot build | pilot verdict | stable build | stable verdict | outcome |",
        "|-------|--------|-------------|---------------|--------------|----------------|---------|",
    ]
    for e in wave.get("items", []):
        lines.append(
            f"| {e['name']} | {e.get('target', '')} "
            f"| {e.get('pilot_build', '')} | {e.get('pilot_verdict', '')} "
            f"| {e.get('stable_build', '')} | {e.get('stable_verdict', '')} "
            f"| {e.get('outcome', '')} |"
        )
    lines.append("")
    lines.append("**итог:** " + json.dumps(wave.get("counts", {}), ensure_ascii=False))
    if wave.get("errors"):
        lines.append("")
        lines.append("**ошибки:**")
        for err in wave["errors"]:
            lines.append(f"- `{err}`")
    return "\n".join(lines) + "\n"


def cmd_plan(args) -> int:
    pkgs = json.loads(Path("pkgs.json").read_text())
    allow_names = parse_allowlist(getattr(args, "recovery_allowlist", None),
                                  pkgs["packages"], getattr(args, "recovery", False))
    # Recovery: цель берётся из точного provenance-пина, а не из refresh дня.
    versions = apply_recovery_pins(load_state_versions(), allow_names)
    chroots = list(pkgs["project"]["chroots"])
    enabled = apply_allowlist(pkgs["packages"], allow_names)
    if allow_names is not None:
        print(f"recovery-allowlist: {allow_names} ({len(enabled)} пакет(ов))")
    owner, pname = api_project(PILOT)
    pilot_hist = history(fetch_builds(owner, pname, args.token or None))
    stable_owner, stable_name = api_project(STABLE)
    stable_hist = history(fetch_builds(stable_owner, stable_name, args.token or None))
    try:
        pilot_probe = probe_all(PILOT, chroots)
        stable_probe = probe_all(STABLE, chroots)
        pilot_pub = pilot_probe["union"]
        stable_pub = stable_probe["union"]
        pilot_done = pilot_probe["completed"]
        stable_done = stable_probe["completed"]
    except WavePlanError as exc:
        print(f"PUBLISH-PROBE-FAILED: {exc}", file=sys.stderr)
        return 1
    plan = build_plan(enabled, versions, pilot_hist, args.max_wave,
                      args.force, stable_hist, pilot_pub, stable_pub,
                      pilot_done, stable_done)
    assert_plan_within_allowlist(plan, allow_names)
    for item in stale_targets(enabled, versions, pilot_pub, stable_pub):
        print(f"  STALE {item['name']}@{item['target']}: в {item['project']} уже "
              f"новая версия, цель из state.json устарела — промоутить нельзя")
    for p in plan:
        print(f"  {p['name']}@{p['target']} mode={p['mode']} "
              f"pilot_build={p['pilot_build'] or '(new)'} "
              f"stable_build={p['stable_build'] or '(new)'} "
              f"pilot_srpm_build={p['pilot_srpm_build'] or '(none)'}")
    print(f"plan_size={len(plan)} pilot={PILOT} stable={STABLE}")
    print(f"published_probe=ok pilot_names={len(pilot_pub)} stable_names={len(stable_pub)}")
    if getattr(args, "json_out", None):
        # Матрица в CI строится по этому файлу: пакеты должны быть независимы.
        Path(args.json_out).write_text(json.dumps(
            {"packages": [{"name": p["name"], "target": p["target"],
                           "mode": p["mode"], "pilot_build": p["pilot_build"],
                           "stable_build": p["stable_build"],
                           # без этого поля immutable-план не может отдать
                           # точный source build fetch'у
                           "pilot_srpm_build": p["pilot_srpm_build"]} for p in plan],
             "plan_size": len(plan),
             "allowlist": allow_names,
             "stale_targets": stale_targets(enabled, versions,
                                            pilot_pub, stable_pub)},
            ensure_ascii=False, indent=2))
    return 0


def load_state_versions() -> dict[str, str]:
    try:
        st = json.loads(Path("state/state.json").read_text())
    except Exception:
        return {}
    return {k: (v.get("ver", "") if isinstance(v, dict) else "") for k, v in st.items()}


def load_enabled_pkgs() -> list[dict]:
    return json.loads(Path("pkgs.json").read_text())["packages"]


def cmd_run(args) -> int:
    pkgs = load_enabled_pkgs()
    allow_names = parse_allowlist(getattr(args, "recovery_allowlist", None),
                                  pkgs, getattr(args, "recovery", False))
    pkgs = apply_allowlist(pkgs, allow_names)
    # Recovery: цель — из точного provenance-пина, а не из refresh дня.
    versions = apply_recovery_pins(load_state_versions(), allow_names)
    chroots = list(json.loads(Path("pkgs.json").read_text())["project"]["chroots"])
    token = args.token or os.environ.get("COPR_API_TOKEN", "")
    copr_conf = args.copr_config or os.environ.get("COPR_CONFIG_PATH", "")

    owner, pname = api_project(PILOT)
    pilot_builds = fetch_builds(owner, pname, token)
    pilot_hist = history(pilot_builds)
    stable_owner, stable_name = api_project(STABLE)
    stable_hist = history(fetch_builds(stable_owner, stable_name, token))

    # Реподата читается ОДНИМ проходом на проект: union (анти-даунгрейд),
    # per-chroot (какие chroot не дособраны) и intersection (цель достигнута
    # только при 4/4). Три таблицы из одного чтения — иначе они могут
    # описывать разные моменты времени.
    try:
        pilot_probe = probe_all(PILOT, chroots)
        stable_probe = probe_all(STABLE, chroots)
    except WavePlanError as exc:
        print(f"PUBLISH-PROBE-FAILED: {exc}", file=sys.stderr)
        return 1
    pilot_pub = pilot_probe["union"]
    stable_pub = stable_probe["union"]
    pilot_done = pilot_probe["completed"]
    stable_done = stable_probe["completed"]

    # P0: план из матрицы — иммутабельный. Package-job получает ровно ту
    # запись, которую plan-job собрал и отдал в matrix. Раньше каждый job
    # заново строил план из ЖИВОГО state.json и живой repodata, то есть план
    # мог уехать между jobs (кто-то успел смерджить, реподата разъехалась),
    # и job делал не то, что было решено. Здесь план не пересобирается вовсе.
    plan_entry = getattr(args, "plan_entry", None)
    only = getattr(args, "only", None)
    if plan_entry:
        try:
            entry = json.loads(plan_entry)
        except json.JSONDecodeError as exc:
            print(f"PLAN-ENTRY-INVALID: {exc}", file=sys.stderr)
            return 1
        absent = [k for k in ("name", "target") if not entry.get(k)]
        if absent:
            print(f"PLAN-ENTRY-INVALID: нет полей {absent}", file=sys.stderr)
            return 1
        entry.setdefault("mode", "full")
        plan = [entry]
        print(f"immutable plan-entry: {entry['name']}@{entry['target']} "
              f"(mode={entry['mode']})")
    else:
        plan = build_plan(pkgs, versions, pilot_hist, args.max_wave, args.force,
                          stable_hist, pilot_pub, stable_pub, pilot_done, stable_done)
        # Per-package запуск (matrix в CI): ограничиваем план одним именем.
        # Если пакета в плане нет — он либо уже зелёный в обоих проектах, либо
        # не приоритизируется. Это НЕ ошибка: job должен завершиться успешно,
        # иначе matrix из-за чужого пакета уедет в failed.
        if only:
            plan = [e for e in plan if e["name"] == only]
            print(f"--only {only}: в плане {len(plan)} пакет(ов)")

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    report_path = Path(args.report_path) if getattr(args, "report_path", None) \
        else log_dir / "wave-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)

    def write_report(wave: dict) -> None:
        report_path.write_text(json.dumps(wave, ensure_ascii=False, indent=2))

    wave = {"fetched_at": int(time.time()), "items": [], "counts": {}, "errors": [],
            "scope": {"only": only or (plan[0]["name"] if plan_entry else None),
                      "max_wave": args.max_wave, "force": args.force,
                      "plan_entry": bool(plan_entry)},
            "published_probe": {"pilot": {k: sorted(v) for k, v in sorted(pilot_pub.items())},
                                "stable": {k: sorted(v) for k, v in sorted(stable_pub.items())}},
            "completed_probe": {"pilot": {k: sorted(v) for k, v in sorted(pilot_done.items())},
                                "stable": {k: sorted(v) for k, v in sorted(stable_done.items())}},
            "stale_targets": stale_targets(pkgs, versions, pilot_pub, stable_pub)}
    if wave["stale_targets"]:
        print(f"STALE targets skipped: {[i['name'] for i in wave['stale_targets']]}",
              file=sys.stderr)
    if not plan:
        if plan_entry:
            # Матрица обещала работу, а плана нет: это баг plan-job, а не
            # «всё хорошо». Молчаливый success здесь означал бы, что пакет
            # молча выпал из публикации.
            print(f"PLAN-ENTRY-MISSING: {plan_entry[:120]}", file=sys.stderr)
            write_report(wave)
            return 1
        if only:
            # Не в волне — отчёт всё равно пишем, чтобы reconcile видел факт.
            wave["items"] = [{"name": only, "outcome": "not-in-wave",
                              "error": f"{only}: не требует публикации "
                                       "(цель достигнута или пакет не приоритизируется)"}]
            wave["counts"] = summarize(wave["items"])
        else:
            print("nothing to do (волна пуста)")
        write_report(wave)
        return 0

    assert_plan_within_allowlist(plan, allow_names)

    pf_ok, pf_reason = preflight_gate(getattr(args, "preflight_json", None))
    if not pf_ok:
        print(f"PREFLIGHT-BLOCKED: {pf_reason}", file=sys.stderr)
        wave["items"] = [{"name": e["name"], "target": e["target"],
                          "outcome": "preflight-blocked", "error": pf_reason}
                         for e in plan]
        wave["counts"] = summarize(wave["items"])
        wave["errors"] = collect_errors(wave["items"])
        write_report(wave)
        return 1

    brake_mod = None
    brake = None
    if getattr(args, "brake_file", None):
        brake_mod = load_retry_brake()
        brake = brake_mod.load(args.brake_file)
        if brake.get("armed"):
            print("BRAKE-ARMED: mirror-brake взведён — submit запрещён",
                  file=sys.stderr)
            wave["items"] = [{"name": e["name"], "target": e["target"],
                              "outcome": "brake-armed",
                              "error": "mirror-brake взведён (повторный сбой зеркала)"}
                             for e in plan]
            wave["counts"] = summarize(wave["items"])
            wave["errors"] = collect_errors(wave["items"])
            write_report(wave)
            return 1

    print(f"wave: {len(plan)} package(s): {[p['name'] for p in plan]}")
    for entry in plan:
        if brake_mod is not None and brake is not None:
            ok, reason = brake_mod.submit_allowed(
                brake, entry["name"], entry["target"],
                cooldown_min=getattr(args, "cooldown_min", 1440))
            if not ok:
                res = dict(entry)
                res["outcome"] = "retry-cooldown"
                res["error"] = reason
                wave["items"].append(res)
                wave["counts"] = summarize(wave["items"])
                write_report(wave)
                print(f"  [BLOCKED] {reason}", flush=True)
                continue
        print(f"=== {entry['name']}@{entry['target']} (mode={entry['mode']}) ===")
        try:
            res = process_package(entry, chroots, token, copr_conf, log_dir,
                                  args.timeout_min, stable_hist, pilot_builds,
                                  pilot_pub, stable_pub,
                                  pilot_probe["per_chroot"],
                                  stable_probe["per_chroot"],
                                  propagate_min=getattr(args, "propagate_min", 10.0))
        except WaveError as exc:
            res = dict(entry)
            res["outcome"] = "pipeline-error"
            res["error"] = str(exc)
            print(f"  [ERROR] {exc}", flush=True)
        wave["items"].append(res)
        print(f"  outcome={res['outcome']}", flush=True)
        # Отчёт перезаписывается после каждого пакета: если job убьёт timeout,
        # уже завершённые пакеты не потеряются.
        wave["counts"] = summarize(wave["items"])
        write_report(wave)

    wave["counts"] = summarize(wave["items"])
    wave["errors"] = collect_errors(wave["items"])
    wave["deferred"] = [e["name"] for e in deferred_items(wave["items"])]
    write_report(wave)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(wave))
    print(json.dumps(wave["counts"], ensure_ascii=False))

    bad = failed_items(wave["items"])
    if bad:
        deferred = {e["name"] for e in deferred_items(wave["items"])}
        hard = [e["name"] for e in bad if e["name"] not in deferred]
        if deferred:
            print(f"DEFERRED (ждали COPR дольше job, resubmit не делаем): "
                  f"{sorted(deferred)}", file=sys.stderr)
        print(f"NOT-ALL-CLEAN: {[e['name'] for e in bad]}"
              + (f" (жёсткие провалы: {hard})" if hard else ""), file=sys.stderr)
        return 1
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="auto-publish.py")
    ap.add_argument("--max-wave", type=int, default=int(os.environ.get("WAVE_MAX", "5")))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--token", default=None)
    ap.add_argument("--copr-config", default=os.environ.get("COPR_CONFIG_PATH", ""))
    ap.add_argument("--timeout-min", type=float, default=180.0)
    ap.add_argument("--only", default=os.environ.get("WAVE_ONLY") or None,
                    help="ограничить волну одним пакетом (matrix в CI)")
    ap.add_argument("--report-path", default=os.environ.get("WAVE_REPORT_PATH") or None,
                    help="куда писать wave-report.json (для per-package job)")
    ap.add_argument("--json-out", default=os.environ.get("WAVE_PLAN_JSON") or None,
                    help="plan: записать план в JSON (вход для matrix)")
    ap.add_argument("--plan-entry", default=os.environ.get("WAVE_PLAN_ENTRY") or None,
                    help="run: исполнить ровно эту запись плана (JSON из matrix), "
                         "не пересобирая план из живого state")
    ap.add_argument("--propagate-min", type=float, default=10.0,
                    help="сколько ждать разъезда repodata после зелёной сборки")
    ap.add_argument("--recovery-allowlist", default=os.environ.get("WAVE_RECOVERY_ALLOWLIST") or None,
                    help="recovery: файл или CSV имён пакетов; фильтр ДО плана")
    ap.add_argument("--recovery", action="store_true",
                    default=bool(os.environ.get("WAVE_RECOVERY")),
                    help="recovery-режим: пустой allowlist => abort (fail-closed)")
    ap.add_argument("--preflight-json", default=os.environ.get("WAVE_PREFLIGHT_JSON") or None,
                    help="run: preflight-evidence; ok!=true => submit запрещён")
    ap.add_argument("--brake-file", default=os.environ.get("WAVE_BRAKE_FILE") or None,
                    help="run: state/retry-brake.json для cooldown/mirror-brake")
    ap.add_argument("--cooldown-min", type=int,
                    default=int(os.environ.get("WAVE_COOLDOWN_MIN", "1440")))
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="preview the wave (no submit)")
    sub.add_parser("run", help="execute the wave")
    return ap.parse_args(argv)


def canonicalize(argv):
    """Флаги можно писать как до, так и после субкоманды
    (`plan --max-wave 5` и `--max-wave 5 plan` — одно и то же)."""
    opts = ("--max-wave", "--force", "--confirm", "--token", "--copr-config",
            "--timeout-min", "--only", "--report-path", "--json-out",
            "--plan-entry", "--propagate-min", "--recovery-allowlist",
            "--preflight-json", "--brake-file", "--cooldown-min", "--recovery")
    head, tail = [], []
    i = 0
    while i < len(argv):
        a = argv[i]
        matched = next((o for o in opts if a == o), None)
        if matched and "=" not in a:
            head.append(a)
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                head.append(argv[i + 1])
                i += 1
            i += 1
            continue
        if any(a.startswith(o) for o in opts):
            head.append(a)
        else:
            tail.append(a)
        i += 1
    return head + tail


def main(argv=None) -> int:
    args = parse_args(canonicalize(list(argv or sys.argv[1:])))
    os.chdir(Path(__file__).resolve().parent.parent)
    if args.force and not args.confirm:
        print("ABORT: --force требует --confirm (force строится только вручную)", file=sys.stderr)
        return 1
    if args.command == "plan":
        try:
            return cmd_plan(args)
        except RecoveryAllowlistError as exc:
            print(f"RECOVERY-ALLOWLIST-ERROR: {exc}", file=sys.stderr)
            return 1
    if args.command == "run":
        try:
            return cmd_run(args)
        except RecoveryAllowlistError as exc:
            print(f"RECOVERY-ALLOWLIST-ERROR: {exc}", file=sys.stderr)
            return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())