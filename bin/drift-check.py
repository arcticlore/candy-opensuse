#!/usr/bin/env python3
"""drift-check.py — required проверка согласованности 4-chroot матрицы.

Сравнивает:
  * declared chroots в pkgs.json (repo);
  * active chroots production COPR (candy-opensuse — stable);
  * active chroots pilot COPR (candy-opensuse-pilot);
  * enable_net production и pilot (должен быть False).

Любое расхождение, недоступность API или неизвестное состояние возвращает
ненулевой код (exit 1) — проверка является REQUIRED, а не warning.

Использование:
  python3 bin/drift-check.py              # stable (production) + pilot
  python3 bin/drift-check.py --project candy-opensuse
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

API = "https://copr.fedorainfracloud.org/api_3"
REQUIRED = {
    "opensuse-tumbleweed-x86_64",
    "opensuse-tumbleweed-aarch64",
    "opensuse-leap-16.0-x86_64",
    "opensuse-leap-16.0-aarch64",
}


class DriftCheckError(Exception):
    pass


def project(name: str) -> dict:
    url = f"{API}/project?ownername=arcticlore&projectname={name}"
    try:
        r = subprocess.run(
            ["curl", "-4", "-s", "--connect-timeout", "10", "--max-time", "30", url],
            capture_output=True, text=True,
        )
    except OSError as e:
        raise DriftCheckError(f"cannot run curl: {e}") from e
    if r.returncode != 0:
        raise DriftCheckError(f"curl rc={r.returncode}: {r.stderr[:200]}")
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise DriftCheckError(f"API ответ не JSON ({name}): {r.stdout[:200]}") from e
    return data


def active_chroots(data: dict) -> set:
    repos = data.get("chroot_repos") or {}
    return set(repos.keys())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None,
                    help="Только один проект (для диагностики); по умолчанию оба")
    args = ap.parse_args()

    repo = json.loads(Path("pkgs.json").read_text())
    repo_chroots = set(repo["project"]["chroots"])

    problems: list[str] = []

    if set(repo_chroots) != REQUIRED:
        problems.append(
            f"pkgs.json declares {sorted(repo_chroots)}, required {sorted(REQUIRED)}"
        )

    checks = []
    if args.project:
        checks = [args.project]
    else:
        checks = ["candy-opensuse", "candy-opensuse-pilot"]

    results = {}
    for name in checks:
        try:
            data = project(name)
        except DriftCheckError as e:
            problems.append(f"{name}: API unknown — {e}")
            continue
        chroots = active_chroots(data)
        enet = data.get("enable_net")
        results[name] = {"chroots": sorted(chroots), "enable_net": enet}
        if chroots != REQUIRED:
            problems.append(
                f"{name}: active chroots {sorted(chroots)} != required {sorted(REQUIRED)}"
            )
        if enet is None:
            problems.append(f"{name}: enable_net unknown (None)")
        elif enet:
            problems.append(f"{name}: enable_net=True (должен быть False)")

    print(json.dumps({
        "repo_declared": sorted(repo_chroots),
        "required": sorted(REQUIRED),
        "projects": results,
        "problems": problems,
    }, indent=2, ensure_ascii=False))

    if problems:
        print("DRIFT-CHECK-FAIL")
        for p in problems:
            print("  -", p)
        return 1
    print("DRIFT-CHECK-OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)