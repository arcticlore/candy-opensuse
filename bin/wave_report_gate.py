#!/usr/bin/env python3
"""wave_report_gate.py — сверка отчётов пакетов с ИСХОДНЫМ планом волны.

Отчёты собираются каждый пакетом отдельно и потом склеиваются в wave-report.
Склейка по имени молча проглатывает дубли и не замечает лишних/пропущенных
пакетов: пока отчёты не сверены с plan.json, итоговая статистика может
утверждать «волна чиста» при расхождении с тем, что plan-job реально решил.

Проверяется ДО state sync (иначе sync уже записал бы в репозиторий то, что
планировалось, а не то, что было опубликовано):
  * plan.json загружен и валиден — ВСЕГДА, даже при нуле отчётов;
  * набор name пакетов в отчётах == набор name в плане;
  * target каждого пакета совпадает с плановым (никакого self'евого target);
  * нет дублей имени ни внутри отчёта, ни между отчётами, ни в самом плане;
  * ноль отчётов допустим только при packages=[] и plan_size=0.

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


def load_plan(path: str) -> tuple[dict[str, str], int | None, list[str]]:
    """-> (name -> target, plan_size, проблемы чтения/валидации плана).

    План обязателен ВСЕГДА — даже когда отчётов ноль. Раньше отсутствие
    plan.json при пустом наборе отчётов считалось успехом, и волна могла
    «успешно» пройти сверку с несуществующим планом.
    """
    p = Path(path)
    if not p.is_file():
        return {}, None, [f"plan.json ({path}) не найден — исходный план волны "
                           "недоступен, сверять не с чем"]
    try:
        data = json.loads(p.read_text() or "{}")
    except ValueError as exc:
        return {}, None, [f"{path} не разбирается как JSON: {exc}"]
    if not isinstance(data, dict):
        return {}, None, [f"{path}: ожидался объект JSON, получен "
                           f"{type(data).__name__}"]
    packages = data.get("packages")
    if not isinstance(packages, list):
        return {}, None, [f"{path}: поле packages отсутствует или не список"]

    problems: list[str] = []
    expected: dict[str, str] = {}
    for pkg in packages:
        if not isinstance(pkg, dict):
            problems.append(f"{path}: элемент плана не объект: {pkg!r}")
            continue
        name = pkg.get("name")
        if not name:
            continue
        if name in expected:
            problems.append(f"план: пакет {name} встречается больше одного раза")
        expected[name] = pkg.get("target")

    size = data.get("plan_size")
    if not isinstance(size, int) or isinstance(size, bool):
        problems.append(f"{path}: plan_size отсутствует или не число "
                        f"({size!r}) — план нельзя считать валидным")
    elif size != len(packages):
        problems.append(f"{path}: plan_size={size} != числу пакетов "
                        f"{len(packages)}")
    return expected, size, problems


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
    expected, _size, plan_problems = load_plan(plan_path)
    reported, report_dupes, files = load_reports(pattern)

    problems: list[str] = []
    problems += list(plan_problems)
    problems += [f"отчёты: пакет {n} встречается больше одного раза"
                 for n in report_dupes]

    # Отсутствующий или битый план — ОШИБКА ВСЕГДА, в том числе когда отчётов
    # ноль: иначе «0 отчётов + нет плана» читается как чистая волна.
    if plan_problems:
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

    # Ноль отчётов допустим ТОЛЬКО при реально загруженном валидном пустом
    # плане (packages=[] и plan_size=0) — это уже гарантировано тем, что
    # plan_problems пуст, а plan_size сверен с числом пакетов. Пустой план при
    # наличии отчётов разобран выше как «лишний пакет».
    if not files and expected:
        problems.append(f"отчётов нет, но план требует {len(expected)} пакет(ов)")
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
