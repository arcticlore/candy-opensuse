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
    fetch_published,
    has_newer,
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


def succeeded_build_url(builds: list[dict], name: str, target: str) -> tuple[int, str] | None:
    """(build_id, srpm_url) последней УСПЕШНОЙ сборки name@target в проекте."""
    for b in sorted(builds, key=lambda x: x.get("id", 0), reverse=True):
        sp = b.get("source_package") or {}
        if sp.get("name") != name or b.get("state") != "succeeded":
            continue
        if ver_base(sp.get("version", "")) != ver_base(target):
            continue
        url = sp.get("url")
        if url:
            return b["id"], url
    return None


def is_prioritizable(pkg: dict) -> bool:
    return str(pkg.get("enabled", "true")).lower() not in ("false", "0")


def prioritize(pkgs: list[dict]):
    """Сортировка кандидатов: prio (ниже = раньше), затем имя — детерминированно."""
    return sorted(pkgs, key=lambda p: (p.get("prio", 5), p["name"]))


def probe_published(project: str, chroots: list[str]) -> dict[str, set[str]]:
    """Что реально опубликовано в проекте, по repodata каждого chroot.

    Fail closed: если реподата недоступна или не разобралась, состояние
    репозитория НЕИЗВЕСТНО, а при неизвестном состоянии любая сборка рискует
    оказаться дубликатом уже опубликованного пакета.
    """
    owner, name = api_project(project)
    try:
        return fetch_published(name, chroots, owner=owner)
    except PublishProbeError as exc:
        raise WavePlanError(f"repodata {project} недоступна: {exc}") from exc


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
) -> list[dict]:
    """Кандидаты волны.

    Пакет в плане, если цель не достигнута зелёной сборкой В ОБОИХ проектах.
    Ключевой случай (был багом): pilot-цель зелёная, stable-цель ещё нет
    (missing/failed/active) — такой пакет обязан попасть в план, иначе
    stable-гэпы не закрываются никогда.

    «Достигнута цель» = зелёная сборка ИЛИ версия есть в repodata: build-list
    COPR неполон (у 133 из 137 опубликованных пакетов нет записи `succeeded`),
    и без repodata план заново пересобирает уже опубликованное.

    Даунгрейд запрещён в обе стороны: если в проекте уже есть версия новее цели,
    пакет выпадает из плана независимо от --force (см. stale_targets).
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
        pilot_ok = target_reached(target, phist) or repodata_has_target(
            pilot_published, name, target)
        stable_ok = target_reached(target, shist) or repodata_has_target(
            stable_published, name, target)
        if not force and pilot_ok and stable_ok:
            continue  # обе цели зелёные — ничего не делаем
        plan.append({
            "name": name,
            "target": target,
            # pilot-сборку продолжаем только если версия совпадает с целью
            "pilot_build": None if pilot_ok else active_build(phist, target),
            "stable_build": None if stable_ok else active_build(shist, target),
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
                     dest_dir: Path) -> tuple[str, str, int]:
    """Скачать ТОТ ЖЕ SRPM из зелёной pilot-сборки. -> (path, sha256, build_id).

    Гарантия «одинаковый SRPM pilot→stable»: promote-only-пакет не пересобирается,
    а переиспользует артефакт, который уже прошёл exact 4/4 в pilot.
    """
    found = succeeded_build_url(builds, name, target)
    if not found:
        raise WaveError(
            f"{name}: pilot-цель {target} зелёная, но SRPM пилот-сборки недоступен — "
            "promote без нарушения byte-идентичности невозможен")
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


def submit(project: str, srpm: str, copr_conf: str | None) -> str:
    """Submit SRPM (copr-cli --nowait). Возвращает числовой build id."""
    cmd = ["copr-cli", "build", "--nowait", project, srpm]
    if copr_conf:
        cmd = ["copr-cli", "--config", copr_conf, "build", "--nowait", project, srpm]
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
) -> dict:
    name = pkg["name"]
    target = pkg["target"]
    out = dict(pkg)
    out.setdefault("mode", "full")

    # Страховка от TOCTOU: план мог быть построен до того, как в репозитории
    # появилась более новая версия. Промоутить старую нельзя ни при каком
    # --force, поэтому исход — провал волны, а не 'promoted'.
    for project, table in ((PILOT, pilot_published), (STABLE, stable_published)):
        if target_is_stale(table, name, target):
            out["outcome"] = "downgrade-blocked"
            out["error"] = (f"{name}: в {project} уже версия новее цели {target} "
                            "— сборка/промоут отменены (обновите state.json)")
            return out

    # --- SRPM: либо переиспользуем артефакт зелёной pilot-сборки, либо собираем
    #     ровно под target из плана. Оба пути проверяют версию ДО submit.
    if pkg.get("mode") == "promote-only":
        try:
            srpm, sha, src_build = fetch_pilot_srpm(
                name, target, pilot_builds, log_dir.parent / "SRPMS")
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

    # --- PILOT stage (пропускается, если pilot-цель уже зелёная) ---
    pilot_bid = pkg.get("pilot_build")
    if out["mode"] != "promote-only":
        if pilot_bid is None:
            try:
                pilot_bid = submit(PILOT, srpm, copr_conf)
                out["pilot_submitted"] = True
            except WaveError as exc:
                out["outcome"] = "pilot-submit-failure"
                out["error"] = str(exc)
                return out
        else:
            out["pilot_resumed"] = True
        out["pilot_build"] = pilot_bid
        rc, seen, verdict = wait_exact_4_4(int(pilot_bid), chroots, token, timeout_min, 30.0)
        out["pilot_rc"] = rc
        out["pilot_verdict"] = verdict
        out["pilot_chroots"] = {c: seen.get(c, "missing") for c in chroots}
        if rc != 0:
            out["outcome"] = "pilot-not-clean"   # не продвигаем в stable ни при каких
            return out
    else:
        out["pilot_ok"] = True

    # --- STABLE stage (тот же SRPM) ---
    stable_bid = pkg.get("stable_build")
    if stable_bid is None:
        try:
            stable_bid = submit(STABLE, srpm, copr_conf)
            out["stable_submitted"] = True
        except WaveError as exc:
            out["outcome"] = "stable-submit-failure"
            out["error"] = str(exc)
            return out
    else:
        out["stable_resumed"] = True
    out["stable_build"] = stable_bid
    rc2, seen2, verdict2 = wait_exact_4_4(int(stable_bid), chroots, token, timeout_min, 30.0)
    out["stable_rc"] = rc2
    out["stable_verdict"] = verdict2
    out["stable_chroots"] = {c: seen2.get(c, "missing") for c in chroots}
    out["outcome"] = "promoted" if rc2 == 0 else "stable-not-clean"
    return out


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
    versions = load_state_versions()
    chroots = list(pkgs["project"]["chroots"])
    owner, pname = api_project(PILOT)
    pilot_hist = history(fetch_builds(owner, pname, args.token or None))
    stable_owner, stable_name = api_project(STABLE)
    stable_hist = history(fetch_builds(stable_owner, stable_name, args.token or None))
    try:
        pilot_pub = probe_published(PILOT, chroots)
        stable_pub = probe_published(STABLE, chroots)
    except WavePlanError as exc:
        print(f"PUBLISH-PROBE-FAILED: {exc}", file=sys.stderr)
        return 1
    plan = build_plan(pkgs["packages"], versions, pilot_hist, args.max_wave,
                      args.force, stable_hist, pilot_pub, stable_pub)
    for item in stale_targets(pkgs["packages"], versions, pilot_pub, stable_pub):
        print(f"  STALE {item['name']}@{item['target']}: в {item['project']} уже "
              f"новая версия, цель из state.json устарела — промоутить нельзя")
    for p in plan:
        print(f"  {p['name']}@{p['target']} mode={p['mode']} "
              f"pilot_build={p['pilot_build'] or '(new)'} "
              f"stable_build={p['stable_build'] or '(new)'}")
    print(f"plan_size={len(plan)} pilot={PILOT} stable={STABLE}")
    print(f"published_probe=ok pilot_names={len(pilot_pub)} stable_names={len(stable_pub)}")
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
    versions = load_state_versions()
    chroots = list(json.loads(Path("pkgs.json").read_text())["project"]["chroots"])
    token = args.token or os.environ.get("COPR_API_TOKEN", "")
    copr_conf = args.copr_config or os.environ.get("COPR_CONFIG_PATH", "")

    owner, pname = api_project(PILOT)
    pilot_builds = fetch_builds(owner, pname, token)
    pilot_hist = history(pilot_builds)
    stable_owner, stable_name = api_project(STABLE)
    stable_hist = history(fetch_builds(stable_owner, stable_name, token))

    # Реподата читается ДО плана: без неё неизвестно, что уже опубликовано,
    # а волна без плана не должна ни собирать, ни промоутить ничего.
    try:
        pilot_pub = probe_published(PILOT, chroots)
        stable_pub = probe_published(STABLE, chroots)
    except WavePlanError as exc:
        print(f"PUBLISH-PROBE-FAILED: {exc}", file=sys.stderr)
        return 1

    plan = build_plan(pkgs, versions, pilot_hist, args.max_wave, args.force,
                      stable_hist, pilot_pub, stable_pub)

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    wave = {"fetched_at": int(time.time()), "items": [], "counts": {}, "errors": [],
            "published_probe": {"pilot": {k: sorted(v) for k, v in sorted(pilot_pub.items())},
                                "stable": {k: sorted(v) for k, v in sorted(stable_pub.items())}},
            "stale_targets": stale_targets(pkgs, versions, pilot_pub, stable_pub)}
    if wave["stale_targets"]:
        print(f"STALE targets skipped: {[i['name'] for i in wave['stale_targets']]}",
              file=sys.stderr)
    if not plan:
        print("nothing to do (волна пуста)")
        (log_dir / "wave-report.json").write_text(
            json.dumps(wave, ensure_ascii=False, indent=2))
        return 0

    print(f"wave: {len(plan)} package(s): {[p['name'] for p in plan]}")
    for entry in plan:
        print(f"=== {entry['name']}@{entry['target']} (mode={entry['mode']}) ===")
        try:
            res = process_package(entry, chroots, token, copr_conf, log_dir,
                                  args.timeout_min, stable_hist, pilot_builds,
                                  pilot_pub, stable_pub)
        except WaveError as exc:
            res = dict(entry)
            res["outcome"] = "pipeline-error"
            res["error"] = str(exc)
            print(f"  [ERROR] {exc}", flush=True)
        wave["items"].append(res)
        print(f"  outcome={res['outcome']}", flush=True)

    wave["counts"] = summarize(wave["items"])
    wave["errors"] = collect_errors(wave["items"])
    (log_dir / "wave-report.json").write_text(json.dumps(wave, ensure_ascii=False, indent=2))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(wave))
    print(json.dumps(wave["counts"], ensure_ascii=False))

    bad = failed_items(wave["items"])
    if bad:
        print(f"NOT-ALL-CLEAN: {[e['name'] for e in bad]}", file=sys.stderr)
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
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="preview the wave (no submit)")
    sub.add_parser("run", help="execute the wave")
    return ap.parse_args(argv)


def canonicalize(argv):
    """Флаги можно писать как до, так и после субкоманды
    (`plan --max-wave 5` и `--max-wave 5 plan` — одно и то же)."""
    opts = ("--max-wave", "--force", "--confirm", "--token", "--copr-config", "--timeout-min")
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
        return cmd_plan(args)
    if args.command == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())