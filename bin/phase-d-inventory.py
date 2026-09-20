#!/usr/bin/env python3
"""phase-d-inventory.py — Phase D: честный per-chroot инвентарь 128×4.

Для каждого enabled-пакета из pkgs.json берём последний билд в COPR
(полная пагинация) и его per-chroot состояния через APIv3 build-chroot/list:

  state per chroot ∈ succeeded / failed / skipped / canceled /
                      importing / pending / running / queued / none
  (отсутствие записи chroot в билде = none; нет билда вовсе = never-built)

Выход:
  reports/opensuse-inventory.json          — счётчики + сводка
  reports/opensuse-inventory-detailed.json — 128 × (4 chroot states)
  reports/opensuse-root-causes.json        — классификация фейлов
  reports/opensuse-root-causes.md          — человекочитаемый отчёт

Политика:
  * API-ошибка при получении списка билдов или состояний ⇒ exit 1,
    никакого «пустого успеха»: build/list и build-chroot/list обязаны
    вернуть валидные данные, иначе отчёт НЕ пишется.
  * В фазе D НЕЛЬЗЯ триггерить билды — скрипт только читает COPR API.
  * Fingerprint пакета: sha256(SPECS/<name>.spec) + source (repo/slug)
    + ecosystem + target version — статически из repo, без стендов.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

OWNER = "arcticlore"
API = "https://copr.fedorainfracloud.org/api_3"
PAGE_LIMIT = 100
MAX_RETRIES = 3
RETRY_BACKOFF = 5.0

ROOT = Path(__file__).resolve().parent.parent
CHROOTS = tuple(
    json.loads((ROOT / "pkgs.json").read_text())["project"]["chroots"]
)

# Состояние записи build-chroot за пределами терминального набора.
CHROOT_STATES = {"succeeded", "failed", "skipped", "canceled",
                 "importing", "pending", "running", "queued"}
TERMINAL_OK = {"succeeded"}
NONTERMINAL = CHROOT_STATES - {"succeeded", "failed", "skipped", "canceled"}


class ApiError(Exception):
    """COPR API недоступен/битый ответ."""


def _request(url: str, token: str | None = None) -> dict:
    """GET + JSON. Retry on 5xx/URLError. Raise ApiError on failure."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ApiError(f"malformed JSON from {url}: {exc}") from exc
            if not isinstance(data, dict):
                raise ApiError(f"unexpected payload shape from {url}"
                               f": {type(data).__name__}")
            return data
        except urllib.error.HTTPError as exc:
            if attempt < MAX_RETRIES and exc.code in (502, 503, 504):
                last_err = exc
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
                continue
            raise ApiError(f"COPR API HTTP {exc.code} at {url}") from exc
        except urllib.error.URLError as exc:
            if attempt < MAX_RETRIES:
                last_err = exc
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
                continue
            raise ApiError(f"COPR API unreachable {url}: {exc.reason}") from exc
    raise ApiError(f"COPR API failed after retries: {last_err}")


def fetch_all_builds(project: str, token: str | None = None) -> list[dict]:
    """Все билды проекта с полной пагинацией. None/пусто — это ошибка."""
    builds: list[dict] = []
    offset = 0
    while True:
        url = (f"{API}/build/list?ownername={OWNER}&projectname={project}"
               f"&limit={PAGE_LIMIT}&offset={offset}")
        data = _request(url, token)
        items = data.get("items")
        if not isinstance(items, list):
            raise ApiError(f"build/list вернул без 'items' (offset={offset})")
        builds.extend(items)
        if len(items) < PAGE_LIMIT:
            return builds
        offset += PAGE_LIMIT
        time.sleep(0.4)


def fetch_chroot_states(build_id: int, token: str | None = None) -> dict[str, str]:
    """{chroot: state} для билда через APIv3 build-chroot/list."""
    url = f"{API}/build-chroot/list?build_id={build_id}&limit=200"
    data = _request(url, token)
    items = data.get("items")
    if not isinstance(items, list):
        raise ApiError(f"build-chroot/list вернул без 'items' "
                       f"(build_id={build_id})")
    states = {}
    for item in items:
        name = item.get("name")
        state = item.get("state")
        if isinstance(name, str) and isinstance(state, str):
            states[name] = state
        elif not isinstance(name, str):
            raise ApiError(f"build-chroot/list worker без имени "
                           f"(build_id={build_id})")
    return states


def sha256_file(path: Path) -> str:
    """SHA-256 содержимого SPEC (сидирует репо-версию манифеста сборки)."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:16]


def load_pkgs() -> tuple[list[dict], dict[str, str]]:
    """(enabled packages, name -> target version из state.json)."""
    pkgs = json.loads((ROOT / "pkgs.json").read_text())["packages"]
    enabled = [p for p in pkgs
               if str(p.get("enabled", "true")).lower() not in ("false", "0")]
    try:
        state = json.loads((ROOT / "state/state.json").read_text())
    except Exception:
        state = {}
    versions = {k: (v.get("ver", "") if isinstance(v, dict) else "")
                for k, v in state.items()}
    return enabled, versions


def spec_fingerprint(name: str) -> str:
    path = ROOT / "SPECS" / f"{name}.spec"
    if path.exists():
        return sha256_file(path)
    return "no-spec-in-repo"


def package_fingerprint(pkg: dict, target_ver: str) -> dict:
    """Статический fingerprint: spec sha + source + ecosystem + target ver."""
    return {
        "eco": pkg.get("eco", ""),
        "host": pkg.get("host", "github"),
        "slug": pkg.get("slug", ""),
        "target_version": target_ver,
        "spec_sha256": spec_fingerprint(pkg["name"]),
    }


def classify_latest(latest: dict | None,
                    chroot_states: dict[str, str]) -> dict[str, str]:
    """Результирующий статус пакета по последнему билду.

    Для пакета без единого билда все chroot = 'never-built'.
    Для каждого chroot: его состояние, или 'none' если chroot не входил в билд.
    """
    if latest is None:
        return {c: "never-built" for c in CHROOTS}
    return {c: chroot_states.get(c, "none") for c in CHROOTS}


def categorize(values: dict[str, str]) -> str:
    """Пакетный статус из четырех per-chroot состояний."""
    vals = set(values.values())
    if not vals:
        return "unknown"
    if vals == {"never-built"}:
        return "never-built"
    if vals <= {"succeeded"}:
        return "succeeded"               # 4/4 или все присутствующие ok
    if any(v in NONTERMINAL for v in vals):
        return "in-progress"
    if "failed" in vals:
        return "failed"
    if vals <= {"skipped", "none", "canceled"}:
        return "edge-only"               # только none/skipped/canceled
    return "mixed"


def summarize_counts(entries: list[dict]) -> dict[str, int]:
    from collections import Counter

    return dict(Counter(e["status"] for e in entries))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default=os.environ.get("COPR_PROJECT", "candy-opensuse-beta"))
    ap.add_argument("--token", default=os.environ.get("COPR_API_TOKEN", ""))
    ap.add_argument("--out", default="reports")
    args = ap.parse_args()

    token = args.token or None
    out = ROOT / args.out
    out.mkdir(exist_ok=True)

    # Шаг 1: полный список билдов. Ошибка ⇒ abort, без отчёта.
    print(f"Fetching all builds of {OWNER}/{args.project} (pagination)...")
    builds = fetch_all_builds(args.project, token)
    if not builds:
        print("[ABORT] build/list вернул пустой список — не отчёт.", file=sys.stderr)
        return 1
    print(f"Total builds fetched: {len(builds)}")

    by_name: dict[str, dict] = {}
    for b in builds:
        sp = b.get("source_package") or {}
        name = sp.get("name", "")
        if not name:
            continue
        cur = by_name.get(name)
        if cur is None or int(b.get("id", 0)) > int(cur.get("id", 0)):
            by_name[name] = b

    # Шаг 2: per-chroot состояния всех последних билдов. Ошибка ⇒ abort.
    print(f"Fetching chroot states for {len(by_name)} latest builds...")
    chroot_map: dict[int, dict[str, str]] = {}
    for bid in sorted({b.get("id") for b in by_name.values()}):
        chroot_map[bid] = fetch_chroot_states(bid, token)
        time.sleep(0.2)
    print(f"Chroot states fetched: {len(chroot_map)} builds")

    pkgs, versions = load_pkgs()
    entries: list[dict] = []
    for pkg in sorted(pkgs, key=lambda p: p["name"]):
        name = pkg["name"]
        latest = by_name.get(name)
        bid = latest.get("id") if latest else None
        values = classify_latest(latest, chroot_map.get(bid, {}) if bid else {})
        entry = {
            "name": name,
            "build_id": bid,
            "status": categorize(values),
            "per_chroot": values,
            "fingerprint": package_fingerprint(pkg, versions.get(name, "")),
        }
        entries.append(entry)

    counts = summarize_counts(entries)
    detailed = {
        "fetched_at": int(time.time()),
        "owner": OWNER,
        "project": args.project,
        "chroots_declared": list(CHROOTS),
        "enabled_packages": len(entries),
        "counts": counts,
        "packages": entries,
    }
    (out / "opensuse-inventory-detailed.json").write_text(
        json.dumps(detailed, ensure_ascii=False, indent=2)
    )

    md_lines = [
        f"# Phase D inventory: {OWNER}/{args.project}",
        "",
        f"Снят: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(detailed['fetched_at']))}",
        f"Chroot, объявленные в проекте: {', '.join(CHROOTS)}",
        f"Enabled-пакетов: **{len(entries)}**",
        "",
        "## Счётчики",
        "",
    ]
    for status, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        md_lines.append(f"- `{status}`: **{cnt}**")
    md_lines += ["", "## Пер-chroot", "", "| пакет | " + " | ".join(CHROOTS) + " | build |", "|-------|" + "|".join(["---"] * len(CHROOTS)) + "|-------|"]
    for e in sorted(entries, key=lambda x: x["name"]):
        md_lines.append("| " + e["name"] + " | " + " | ".join(e["per_chroot"].get(c, "none") for c in CHROOTS) + f" | {e['build_id']} |")
    (out / "opensuse-inventory.md").write_text("\n".join(md_lines) + "\n")

    summary = {k: detailed[k] for k in ("fetched_at", "project", "chroots_declared", "enabled_packages", "counts")}
    (out / "opensuse-inventory.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2)
    )

    print("OK: reports/opensuse-inventory.json + -detailed.json + .md")
    print(f"Counts: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())