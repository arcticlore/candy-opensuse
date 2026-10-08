#!/usr/bin/env python3
"""wave_sync_gate.py — гейт точного diff'а синхронизации state/SPECS ДО коммита.

Раньше гейт выполнялся ПОСЛЕ git commit/push: `git status` уже был чистым,
поэтому проверка фактически подтверждала пустое дерево и ничего не говорила
о том, что волна собиралась закоммитить. Здесь diff проверяется в рабочем
дереве, между генерацией и коммитом.

Разрешённые изменения — ровно два вида, ничего больше:
  * state/state.json, и только для promoted-пакетов, причём `ver` обязан
    совпадать с exact target этой волны;
  * SPECS/<name>.spec для promoted-пакета.

Любой другой spec (в том числе случайно перегенерированный чужой) или вообще
любой другой путь => exit 1, reconcile падает, ничего не коммитится.

База по умолчанию HEAD, а не origin/master: ветка волны сама отличается от
master, и сравнение с master показало бы её собственные файлы как
«посторонние». Нужен ровно тот коммит, на котором стоит checkout — тогда в
diff попадает только то, что изменила генерация.

Использование:
    python3 bin/wave_sync_gate.py --promoted "dysk@3.7.1 dua@2.32.0" \\
        --base HEAD
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


# Untracked-каталоги, которые создаёт сам job: артефакты волны и её логи.
# Их нет в .gitignore, но и в коммит они попасть не могут — commit шаг делает
# `git add -A -- state/state.json SPECS/ pkgs.json`. Без фильтра каждый reconcile
# падал бы на "посторонний путь: collected/" и волна не смогла бы закоммитить.
SCRATCH_PREFIXES = ("logs/", "collected/", "plan-artifact/")


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def changed_paths(base: str) -> list[str]:
    """Все изменённые пути относительно base, включая неотслеживаемые."""
    paths: set[str] = set()
    # staged + unstaged
    out = _git("diff", "--name-only", base)
    paths.update(line.strip() for line in out.splitlines() if line.strip())
    # untracked: новые спеки сюда попадают, мусор job'а — нет
    out = _git("ls-files", "--others", "--exclude-standard")
    paths.update(line.strip() for line in out.splitlines()
                 if line.strip() and not line.strip().startswith(SCRATCH_PREFIXES))
    # удалённые
    out = _git("diff", "--name-only", "--diff-filter=D", base)
    paths.update(line.strip() for line in out.splitlines() if line.strip())
    return sorted(paths)


def parse_promoted(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for tok in (raw or "").split():
        name, _, ver = tok.partition("@")
        if name and ver:
            result[name] = ver
    return result


def state_entries(base: str) -> dict[str, dict]:
    try:
        text = _git("show", f"{base}:state/state.json")
    except SystemExit:
        return {}
    try:
        return json.loads(text or "{}")
    except ValueError:
        return {}


def violations(base: str, promoted: dict[str, str]) -> list[str]:
    problems: list[str] = []
    if not promoted:
        return ["promoted-список пуст — сверять нечего (это ошибка вызова)"]

    allowed = {"state/state.json"} | {f"SPECS/{n}.spec" for n in promoted}
    paths = changed_paths(base)
    for path in paths:
        if path not in allowed:
            problems.append(f"посторонний путь в sync-diff: {path}")

    # --- state.json: только promoted-ключи, только exact target ---
    before = state_entries(base)
    try:
        after = json.loads(Path("state/state.json").read_text() or "{}")
    except (OSError, ValueError) as exc:
        return problems + [f"state/state.json не читается: {exc}"]

    for name in sorted(set(before) | set(after)):
        if name in promoted:
            continue
        if before.get(name) != after.get(name):
            problems.append(f"state: не promoted-пакет {name} изменён в state.json")

    for name, target in sorted(promoted.items()):
        entry = after.get(name)
        if not isinstance(entry, dict):
            problems.append(f"state: promoted {name} отсутствует в state.json")
            continue
        if entry.get("ver") != target:
            problems.append(
                f"state: {name} ver={entry.get('ver')!r} != exact target {target!r}")

    # --- SPECS: изменённые спеки только promoted-пакетов ---
    spec_names = {p[len("SPECS/"):-len(".spec")]
                  for p in paths
                  if p.startswith("SPECS/") and p.endswith(".spec")}
    for name in sorted(spec_names - set(promoted)):
        problems.append(f"спека не-promoted пакета изменена: SPECS/{name}.spec")

    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--promoted", required=True,
                    help="через пробел: name@target")
    ap.add_argument("--base", default="HEAD",
                    help="база сравнения: HEAD = только изменения генерации")
    args = ap.parse_args(argv)

    promoted = parse_promoted(args.promoted)
    problems = violations(args.base, promoted)
    if problems:
        print("WAVE-SYNC-GATE: FAIL", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"WAVE-SYNC-GATE: ok ({', '.join(sorted(promoted))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
