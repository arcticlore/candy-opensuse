#!/usr/bin/env python3
"""copr-baseline.py — снять честный baseline проекта arcticlore/candy-opensuse
с COPR API (полная пагинация) и классифицировать причины фейлов из логов билдеров.

Выход: reports/opensuse-baseline.json + reports/opensuse-baseline.md.

Классификация failure-причин по builder-live.log.gz (для каждого failed билда,
по обоим Tumbleweed chroot):
  * builddep-unresolved  — «No match for argument: <BR>» на стадии builddep
  * builddep-timeout     — таймаут/мёртвая загрузка пакетов на builddep
  * build-phase-error    — упало уже в %build/%install (не builddep)
  * log-missing         — лог недоступен (404/сетевая ошибка)

Per-chroot статус по списку билдов получить нельзя (build/state общий на всё
задание), поэтому причина классифицируется записями логов обоих Tumbleweed
chroot; builddep-фейлы с «No match» качаются на каждый chroot отдельно.
"""

from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

OWNER = "arcticlore"
PROJECT = "candy-opensuse"
API = "https://copr.fedorainfracloud.org/api_3"
DL = "https://download.copr.fedorainfracloud.org/results"
PAGE_LIMIT = 100
CHROOTS = tuple(json.loads(Path("pkgs.json").read_text())["project"]["chroots"])
LOG_DIR = Path("logs/builder-baseline")


def api(extra: str):
    url = f"{API}/build/list?{extra}"
    r = subprocess.run(
        ["curl", "-4", "-s", "--connect-timeout", "10", "--max-time", "30", url],
        capture_output=True, text=True, timeout=35,
    )
    try:
        return json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def fetch_all_builds() -> list[dict] | None:
    all_builds: list[dict] = []
    offset = 0
    while True:
        data = api(
            f"ownername={OWNER}&projectname={PROJECT}&limit={PAGE_LIMIT}&offset={offset}"
        )
        if not data or "items" not in data:
            return None
        items = data["items"]
        all_builds.extend(items)
        if len(items) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
        time.sleep(0.4)
    return all_builds


def fetch_log(build_id: int, name: str, chroot: str) -> str | None:
    """Скачать и распаковать builder-live.log.gz. Кэширует имена файлов."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cache = LOG_DIR / f"{build_id}-{name}-{chroot.replace('/', '_')}.log"
    if cache.exists():
        return cache.read_text(errors="replace")
    gz = f"{DL}/{OWNER}/{PROJECT}/{chroot}/{build_id}-{name}/builder-live.log.gz"
    r = subprocess.run(
        ["curl", "-4", "-sfL", "--connect-timeout", "10", "--max-time", "60", gz],
        capture_output=True, text=False, timeout=70,
    )
    if r.returncode != 0 or not r.stdout:
        (LOG_DIR / f"{build_id}-{name}-{chroot}.nomissing").write_text("")
        return None
    try:
        import gzip
        text = gzip.decompress(r.stdout).decode("utf-8", errors="replace")
    except gzip.BadGzipFile:
        text = r.stdout.decode("utf-8", errors="replace")
    cache.write_text(text, errors="replace")
    return text


def classify_log(text: str) -> str:
    """Классифицировать одиночный лог билдера."""
    if not text:
        return "log-missing"
    lines = text.splitlines()
    no_match = [l for l in lines if "No match for argument" in l]
    if no_match:
        return "builddep-unresolved"
    # фатал-ошибка может идти уже в собственно сборке
    if any("Error" in l and "in %" in l for l in lines) or "RPM build errors" in lines:
        return "build-phase-error"
    if any("dnf5 builddep" in l or "builddep failed" in l for l in lines):
        return "builddep-timeout"
    if any("Failed to resolve the transaction" in l for l in lines):
        return "builddep-unresolved"
    if any("ERROR" in l for l in lines):
        return "build-phase-error"
    return "other"


def missing_br(text: str) -> list[str]:
    """Список незарезолвленных BuildRequires из «No match for argument: X»."""
    out = []
    for l in text.splitlines():
        if "No match for argument" in l:
            arg = l.split("No match for argument:", 1)[1].strip()
            if arg and arg not in out:
                out.append(arg)
    return out


def main() -> int:
    os.chdir(Path(__file__).resolve().parent.parent)
    REPORTS = Path("reports")
    REPORTS.mkdir(exist_ok=True)

    all_builds = fetch_all_builds()
    if all_builds is None:
        print("[ABORT] COPR API недоступен.", file=sys.stderr)
        return 1

    pkgs = json.loads(Path("pkgs.json").read_text())["packages"]
    try:
        state = json.loads(Path("state/state.json").read_text())
    except Exception:
        state = {}
    enabled = {p["name"] for p in pkgs if str(p.get("enabled", "true")).lower() not in ("false", "0")}

    by_name: dict[str, list[dict]] = {}
    for b in all_builds:
        by_name.setdefault(b["source_package"]["name"], []).append(b)
    for bs in by_name.values():
        bs.sort(key=lambda b: b["id"])

    # последний билд каждого пакета + история
    latest: dict[str, dict] = {}
    ever_ok: set[str] = set()
    for n, bs in by_name.items():
        latest[n] = bs[-1]
        if any(b["state"] == "succeeded" for b in bs):
            ever_ok.add(n)

    missing = sorted(enabled - set(latest))
    chroot_usage: Counter = Counter()
    leap_builds = 0
    for b in all_builds:
        for c in b.get("chroots", []):
            chroot_usage[c] += 1
        if any("leap" in c for c in b.get("chroots", [])):
            leap_builds += 1

    reasons: dict[str, Counter] = {}
    br_hits: Counter = Counter()
    per_build: list[dict] = []
    for n, b in sorted(latest.items()):
        if b["state"] != "failed":
            continue
        chroots = [c for c in CHROOTS if c in b["chroots"]]
        entry = {"name": n, "build_id": b["id"], "version": b["source_package"].get("version", "")}
        reason = None
        brs = []
        for ch in chroots or [CHROOTS[0]]:
            log = fetch_log(b["id"], n, ch)
            r = classify_log(log)
            entry.setdefault("chroot_logs", {})[ch] = r
            if r == "builddep-unresolved":
                brs += missing_br(log or "")
            if reason is None:
                reason = r
        if reason is None:
            reason = "log-missing"
        entry["reason"] = reason
        entry["missing_br"] = sorted(set(brs))[:10]
        if reason == "builddep-unresolved" and brs:
            for x in set(brs):
                br_hits[x] += 1
        reasons.setdefault(reason, Counter())[f"{n}@{b['source_package'].get('version','')}"] = 1
        per_build.append(entry)

    report = {
        "fetched_at": int(time.time()),
        "owner": OWNER, "project": PROJECT,
        "chroots_declared": CHROOTS,
        "chroot_usage_in_builds": dict(chroot_usage),
        "leap_chroot_builds": leap_builds,
        "counts": {
            "enabled_in_pkgs_json": len(enabled),
            "with_builds_in_copr": len(latest),
            "missing_from_copr": len(missing),
            "latest_succeeded": sum(1 for b in latest.values() if b["state"] == "succeeded"),
            "latest_failed": sum(1 for b in latest.values() if b["state"] == "failed"),
            "ever_succeeded": len(ever_ok),
            "never_succeeded": len(enabled - ever_ok),
        },
        "missing_packages": missing,
        "failure_reasons": {k: len(v) for k, v in sorted(reasons.items(), key=lambda x: -sum(x[1].values()))},
        "unresolved_buildrequires": dict(br_hits.most_common(30)),
        "failed_builds": per_build,
    }

    (REPORTS / "opensuse-baseline.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )

    # Markdown
    md = []
    md.append(f"# Baseline: {OWNER}/{PROJECT}\n")
    md.append(f"Снят: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(report['fetched_at']))}  ")
    md.append(f"Chroot, объявленные в проекте: {', '.join(CHROOTS)}  ")
    md.append(f"Всего chroot-вхождений в билдах: {dict(chroot_usage)}  ")
    md.append(f"Билдов с включённым Leap: {leap_builds}  ")
    md.append("")
    md.append("## Счётчики")
    md.append("")
    c = report["counts"]
    md.append(f"- enabled в pkgs.json: **{c['enabled_in_pkgs_json']}**")
    md.append(f"- есть билды в COPR: **{c['with_builds_in_copr']}**")
    md.append(f"- enabled без билдов (missing): **{c['missing_from_copr']}**"
              f" → `{', '.join(report['missing_packages'])}`")
    md.append(f"- последний билд succeeded: **{c['latest_succeeded']}** / failed: **{c['latest_failed']}**")
    md.append(f"- хоть раз succeeded-когда-либо: **{c['ever_succeeded']}**")
    md.append(f"- никогда не собирался зелёным: **{c['never_succeeded']}**")
    md.append("")
    md.append("## Причины фейлов (последние билды)")
    md.append("")
    for k, v in sorted(report["failure_reasons"].items(), key=lambda x: -x[1]):
        md.append(f"- `{k}`: **{v}**")
    md.append("")
    md.append("## Незарезолвленные BuildRequires (по логам)")
    md.append("")
    md.append("| BR | пакетов с фейлом на нём |")
    md.append("|----|----|")
    for br, cnt in br_hits.most_common(30):
        md.append(f"| `{br}` | {cnt} |")
    md.append("")
    md.append("## Фейлы по пакетам")
    md.append("")
    md.append("| пакет | build | версия | причина | missing_br |")
    md.append("|-------|-------|--------|---------|------------|")
    for e in sorted(per_build, key=lambda x: (x["reason"], x["name"])):
        brs = ", ".join(e.get("missing_br", [])) or ""
        md.append(f"| {e['name']} | {e['build_id']} | {e['version']} | {e['reason']} | {brs} |")
    md.append("")

    (REPORTS / "opensuse-baseline.md").write_text("\n".join(md))
    print(f"OK: reports/opensuse-baseline.json + .md")
    return 0


if __name__ == "__main__":
    sys.exit(main())