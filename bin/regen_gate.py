#!/usr/bin/env python3
"""regen_gate.py — гейт «точный реген-дифф» перед auto-merge бот-PR.

Прошлая проверка (в update.yml) смотрела ТОЛЬКО на идемпотентность двух
прогонов gen_specs. Этого недостаточно: бот-PR с любыми правками вне
SPECS/state/pkgs.json проходил как «чистый», а удалённые/лишние SPECS
(например spec отключённого пакета) оставались незамеченными.

Проверяем три инварианта (exit != 0 при нарушении любого):
  1. SCOPE: изменённые файлы ⊆ {SPECS/*, state/state.json, pkgs.json}
     (используется git status, если репозиторий доступен; иначе — тихая
     деградация с явным предупреждением, т.к. в контейнере .git может отсутствовать).
  2. SPEC SET: SPECS/ содержит ровно spec каждого enabled-пакета из pkgs.json
     (нет stale-spec отключённых пакетов, нет enabled-пакета без spec).
  3. IDEMPOTENCY: два подряд прогона `gen_specs.py --all` не меняют SPECS/.

Использование:
    python3 bin/regen_gate.py
В CI печатает `regen_gate=ok` либо `regen_gate=fail:<reason>`.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ALLOWED_PREFIXES = ("SPECS/",)
ALLOWED_EXACT = ("state/state.json", "pkgs.json")

# Untracked-каталоги, которые создаёт сам job (artifacts, логи волны) и которых
# нет в .gitignore. Это НЕ изменения репозитория: коммит берёт только
# state/state.json, SPECS/ и pkgs.json, поэтому мусор рабочего каталога не может
# попасть в него. Без этого списка git status в reconcile всегда непуст, и
# scope-проверка выдавала бы вечный fail на чужом мусоре.
#
# preflight/ — страховка: evidence теперь качается в $RUNNER_TEMP, но если он
# когда-нибудь снова окажется в дереве, узкий точный префикс не даст ему ни
# уронить scope-гейт, ни попасть в коммит (коммит берёт только разрешённые пути).
SCRATCH_PREFIXES = ("logs/", "collected/", "plan-artifact/", "preflight/")


def enabled_names(pkgs_path: str | Path) -> set[str]:
    data = json.loads(Path(pkgs_path).read_text())
    return {p["name"] for p in data["packages"]
            if str(p.get("enabled", "true")).lower() not in ("false", "0")}


def specs_present(specs_dir: str | Path) -> set[str]:
    d = Path(specs_dir)
    if not d.is_dir():
        return set()
    return {f.name[:-len(".spec")] for f in d.iterdir()
            if f.is_file() and f.name.endswith(".spec")}


def check_scope(changed: list[str], allowed_prefixes=ALLOWED_PREFIXES,
                allowed_exact=ALLOWED_EXACT) -> list[str]:
    """Вернуть список нарушений scope (пусто == всё в ожидаемых путях)."""
    bad = []
    for path in changed:
        norm = path.strip().strip('"')
        parts = norm.split("/")
        # путь вне дерева репозитория не может быть «ожидаемым»
        if ".." in parts or norm.startswith("/"):
            bad.append(norm)
            continue
        if norm in allowed_exact:
            continue
        if any(norm.startswith(p) for p in allowed_prefixes):
            continue
        bad.append(norm)
    return sorted(bad)


def check_spec_set(specs: set[str], enabled: set[str]) -> list[str]:
    """Вернуть список нарушений соответствия SPECS/ ↔ enabled-пакетам."""
    return sorted(f"stale-spec:{n}" for n in specs - enabled) + \
           sorted(f"missing-spec:{n}" for n in enabled - specs)


def tree_hash(specs_dir: str | Path) -> str:
    h = hashlib.sha256()
    for path in sorted(Path(specs_dir).rglob("*")):
        if path.is_file():
            h.update(str(path.relative_to(specs_dir)).encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def git_changed_paths(root: str | Path) -> list[str] | None:
    """Список изменённых путей из git status; None — если git недоступен."""
    try:
        r = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=120)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        # porcelain: "XY PATH" — ровно 3 символа статуса, ведущие пробелы значимы
        payload = line[3:] if len(line) > 3 else ""
        # рено/копи: "R  old -> new" — интересует новый путь
        parts = payload.split(" -> ")
        path = parts[-1].strip().strip('"')
        if not path:
            continue
        # untracked-мусор job'а не считаем изменением репозитория
        if line[:2] == "??" and path.startswith(SCRATCH_PREFIXES):
            continue
        out.append(path)
    return out


def run_gen(root: str | Path) -> None:
    subprocess.run([sys.executable, "bin/gen_specs.py", "--all"], cwd=str(root),
                   check=True, capture_output=True, text=True, timeout=900)


def main(argv=None) -> int:
    argv = list(argv or sys.argv[1:])
    root = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parent.parent
    failures: list[str] = []

    # 1. SCOPE
    changed = git_changed_paths(root)
    if changed is None:
        print("WARN: git status недоступен — SCOPE-проверка пропущена "
              "(в контейнере .git может отсутствовать)", file=sys.stderr)
    else:
        bad = check_scope(changed)
        if bad:
            failures.append(f"scope: неожиданные пути {bad}")
        else:
            print(f"scope: ok ({len(changed)} изменённых путей)")

    # 2. SPEC SET
    enabled = enabled_names(root / "pkgs.json")
    problems = check_spec_set(specs_present(root / "SPECS"), enabled)
    if problems:
        failures.append(f"spec-set: {problems}")
    else:
        print(f"spec-set: ok ({len(enabled)} enabled == {len(specs_present(root / 'SPECS'))} specs)")

    # 3. IDEMPOTENCY
    before = tree_hash(root / "SPECS")
    run_gen(root)
    mid = tree_hash(root / "SPECS")
    run_gen(root)
    after = tree_hash(root / "SPECS")
    if before == mid == after:
        print("idempotency: ok")
    else:
        failures.append("idempotency: gen_specs.py --all меняет SPECS при повторных прогонах")

    if failures:
        print("regen_gate=fail:" + "; ".join(failures), file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("regen_gate=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())