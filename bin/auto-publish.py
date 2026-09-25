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

Resume/продолжение после таймаута НЕ дублирует submit: активные сборки в pilot
и stable перехватываются по существующему build id, по ним просто продолжается
наблюдение (см. selected active_build).

Локальные make-srpm-skip'ы и «уже готово» фиксируются в отчёте и НЕ роняют run:
fallback-решения курируются человеком через logs/wave-report.json.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

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


def needs_pilot(name: str, target: str, hist: list[tuple[str, str, int]]) -> bool:
    """Пакет нужен в pilot, если цель не достигнута зелёным (или никогда не собирался)."""
    if not hist:
        return True
    for state, ver, _bid in hist:
        if state == "succeeded":
            base = ver.split("-", 1)[0] if "-" in ver else ver
            return base != target
    return True


def active_build(hist: list[tuple[str, str, int]]) -> int | None:
    """Самый свежий НЕтерминальный билд пакета (для resume, без дубликата submit)."""
    for state, _ver, bid in hist:
        if state in ACTIVE:
            return bid
    return None


def is_prioritizable(pkg: dict) -> bool:
    return str(pkg.get("enabled", "true")).lower() not in ("false", "0")


def prioritize(pkgs: list[dict]):
    """Сортировка кандидатов: prio (ниже = раньше), затем имя — детерминированно."""
    return sorted(pkgs, key=lambda p: (p.get("prio", 5), p["name"]))


def build_plan(
    pkgs: list[dict],
    versions: dict[str, str],
    pilot_hist: dict[str, list[tuple[str, str, int]]],
    max_wave: int,
    force: bool = False,
) -> list[dict]:
    """Список кандидатов волны: только не достигшие цели в pilot (или force)."""
    if max_wave < 1:
        raise WavePlanError("max_wave должен быть >= 1")
    plan: list[dict] = []
    for p in prioritize(pkgs):
        name = p["name"]
        target = versions.get(name, "")
        if not target:
            continue
        if not is_prioritizable(p):
            continue
        hist = pilot_hist.get(name, [])
        if not force and not needs_pilot(name, target, hist):
            continue
        plan.append({
            "name": name,
            "target": target,
            "pilot_build": active_build(hist),
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


def make_srpm(name: str, log_dir: Path) -> str:
    """Свежий SRPM: удаление stale + make-srpm.sh. Возвращает путь SRPM.

    raise WaveError на fail; возвращает '' если make-srpm честно пометил SKIP.
    """
    for f in log_dir.parent.glob(f"SRPMS/{name}-*.src.rpm"):
        f.unlink()
    log = log_dir / f"srpm-{name}.log"
    rc = subprocess.run(
        ["bin/make-srpm.sh", name],
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
    return str(srpms[0])


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
) -> dict:
    name = pkg["name"]
    out = dict(pkg)
    srpm = make_srpm(name, log_dir)
    if not srpm:
        out["outcome"] = "srpm-skip"
        return out

    # --- PILOT stage ---
    pilot_bid = pkg.get("pilot_build")
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
        out["outcome"] = "pilot-not-clean"      # не продвигаем в stable ни при каких
        return out

    # --- STABLE stage (тот же SRPM) ---
    active = active_build(stable_hist.get(name, []))
    stable_bid = active
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
    owner, pname = api_project(PILOT)
    pilot_hist = history(fetch_builds(owner, pname, args.token or None))
    plan = build_plan(pkgs["packages"], versions, pilot_hist, args.max_wave, args.force)
    for p in plan:
        print(f"  {p['name']}@{p['target']} pilot_build={p['pilot_build'] or '(new)'}")
    print(f"plan_size={len(plan)} pilot={PILOT}")
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
    pilot_hist = history(fetch_builds(owner, pname, token))
    plan = build_plan(pkgs, versions, pilot_hist, args.max_wave, args.force)

    stable_owner, stable_name = api_project(STABLE)
    stable_hist = history(fetch_builds(stable_owner, stable_name, token))

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    wave = {"fetched_at": int(time.time()), "items": [], "counts": {}, "errors": []}
    if not plan:
        print("nothing to do (волна пуста)")
        return 0

    print(f"wave: {len(plan)} package(s): {[p['name'] for p in plan]}")
    for entry in plan:
        print(f"=== {entry['name']}@{entry['target']} ===")
        try:
            res = process_package(entry, chroots, token, copr_conf, log_dir,
                                  args.timeout_min, stable_hist)
        except WaveError as exc:
            res = dict(entry)
            res["outcome"] = "pipeline-error"
            res["error"] = str(exc)
            print(f"  [ERROR] {exc}", flush=True)
        wave["items"].append(res)
        print(f"  outcome={res['outcome']}", flush=True)

    wave["counts"] = summarize(wave["items"])
    wave["errors"] = [
        f"{e['name']}: {e.get('error')}" for e in wave["items"]
        if e.get("error") and e.get("outcome") != "pipeline-error"
    ]
    (log_dir / "wave-report.json").write_text(json.dumps(wave, ensure_ascii=False, indent=2))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(render_markdown(wave))
    print(json.dumps(wave["counts"], ensure_ascii=False))

    bad = [e["name"] for e in wave["items"]
           if e["outcome"] not in ("promoted", "srpm-skip")]
    fails = [e["name"] for e in wave["items"]
             if e["outcome"] not in ("promoted", "srpm-skip")]
    if bad:
        print(f"NOT-ALL-CLEAN: {fails}", file=sys.stderr)
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


def main(argv=None) -> int:
    args = parse_args(argv)
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