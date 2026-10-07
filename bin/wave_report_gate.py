#!/usr/bin/env python3
"""wave_report_gate.py — сверка отчётов пакетов с ИСХОДНЫМ планом волны.

Отчёты собираются каждый пакетом отдельно и потом склеиваются в wave-report.
Склейка по имени молча проглатывает дубли и не замечает лишних/пропущенных
пакетов: пока отчёты не сверены с plan.json, итоговая статистика может
утверждать «волна чиста» при расхождении с тем, что plan-job реально решил.

Проверяется ДО state sync (иначе sync уже записал бы в репозиторий то, что
планировалось, а не то, что было опубликовано):
  * набор name пакетов в отчётах == набор name в плане;
  * target каждого пакета совпадает с плановым (никакого self'евого target);
  * нет дублей имени ни внутри отчёта, ни между отчётами, ни в самом плане;
  * отчётов без плана не бывает.

exit 0 — расхождений нет; exit 1 — есть, со списком причин.

Использование:
    python3 bin/wave_report_gate.py --plan plan-artifact/plan.json \\
        --reports 'logs/report-*.json'
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path


def load_plan(path: str) -> tuple[dict[str, str], list[str]]:
    """-> (name -> target, список имён-дублей в плане)."""
    p = Path(path)
    if not p.is_file():
        return {}, []
    data = json.loads(p.read_text() or "{}")
    expected: dict[str, str] = {}
    dupes: list[str] = []
    for pkg in data.get("packages") or []:
        name = pkg.get("name")
        if not name:
            continue
        if name in expected:
            dupes.append(name)
        expected[name] = pkg.get("target")
    return expected, dupes


def load_reports(pattern: str) -> tuple[dict[str, str], list[str], list[str]]:
    """-> (name -> target, дубли имён, файлы отчётов)."""
    found: dict[str, str] = {}
    dupes: list[str] = []
    files = sorted(glob.glob(pattern))
    for path in files:
        try:
            data = json.loads(Path(path).read_text() or "{}")
        except (OSError, ValueError) as exc:
            dupes.append(f"<нечитаемый {path}: {exc}>")
            continue
        for item in data.get("items") or []:
            name = item.get("name")
            if not name:
                continue
            if name in found:
                dupes.append(name)
            found[name] = item.get("target")
    return found, dupes, files


def reconcile(plan_path: str, pattern: str) -> list[str]:
    """Вернуть список нарушений (пусто == отчёты точно соответствуют плану)."""
    expected, plan_dupes = load_plan(plan_path)
    reported, report_dupes, files = load_reports(pattern)

    problems: list[str] = []
    problems += [f"план: пакет {n} встречается больше одного раза" for n in plan_dupes]
    problems += [f"отчёты: пакет {n} встречается больше одного раза"
                 for n in report_dupes]

    if not Path(plan_path).is_file():
        if files:
            problems.append(
                f"есть {len(files)} отчёт(ов), но {plan_path} не найден — "
                "сверять не с чем, итог недостоверен")
        return problems

    for name in sorted(set(reported) - set(expected)):
        problems.append(f"лишний пакет в отчётах: {name} (его нет в плане)")
    for name in sorted(set(expected) - set(reported)):
        problems.append(f"пропущенный пакет: {name} есть в плане, отчёта нет")
    for name in sorted(set(expected) & set(reported)):
        if expected[name] != reported[name]:
            problems.append(
                f"target mismatch у {name}: план {expected[name]!r}, "
                f"отчёт {reported[name]!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", required=True, help="путь к plan.json из wave-plan artifact")
    ap.add_argument("--reports", required=True, help="glob отчётов пакетов")
    args = ap.parse_args(argv)

    problems = reconcile(args.plan, args.reports)
    if problems:
        print("WAVE-REPORT-GATE: FAIL", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("WAVE-REPORT-GATE: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
