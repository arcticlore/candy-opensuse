#!/usr/bin/env python3
"""merge_gate.py — fail-closed наблюдение за bot-PR после его создания.

Зачем: пул-реквест, открытый встроенным GITHUB_TOKEN, сам не порождает CI
(события от GITHUB_TOKEN не триггерят workflow runs), а auto-merge без
доказанных required checks либо не сработает, либо сольёт не то.

Поэтому после push ветки и `gh pr create` волна обязана:

  dispatch      — ЯВНО запустить validate.yml на точной bot-ветке и убедиться,
                  что голова ветки не ушла с head SHA PR;
  wait-checks   — дождаться ВСЕХ required checks и доказать, что каждый из них
                  относится именно к head SHA PR (а не к master/другому SHA);
  observe-merged— дождаться фактического verdict `merged`.

Любая неопределённость = провал (GateError → exit 1): отказ API, битый JSON,
отсутствующий required check по таймауту, PR закрыт без мержа, смена head SHA.
Личный PAT/ deploy key не используются нигде — только run-scoped GITHUB_TOKEN.

Все зависимости (gh, sleep, clock) внедряются, чтобы гейт тестировался офлайн.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Sequence

# Заключения, означающие «check прошёл». neutral/skipped — легитимные
# пропуски (например, job с `if`, который не сработал).
OK_CONCLUSIONS = frozenset({"success", "neutral", "skipped"})
# Заключения, означающие «check завершился отказом» — ждать бессмысленно.
BAD_CONCLUSIONS = frozenset({
    "failure", "cancelled", "timed_out", "action_required",
    "startup_failure", "stale", "error",
})
# Ещё бегущие/не начавшиеся.
PENDING_CONCLUSIONS = frozenset({None, "", "queued", "in_progress", "waiting",
                                 "requested", "pending", "rerequested"})

DEFAULT_CHECKS_FILE = "ci/required-checks.json"
DEFAULT_WORKFLOW = "validate.yml"


class GateError(RuntimeError):
    """Неопределённость или явный отказ — публикация/мерж запрещены."""


def _run(argv: Sequence[str], timeout: int = 90) -> str:
    """Запуск внешней команды: любой ненулевой код = GateError, не warning."""
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GateError(f"не удалось выполнить {' '.join(argv[:2])}: {exc}") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise GateError(
            f"{' '.join(argv[:3])} завершился с rc={proc.returncode}: {err[:400]}")
    return proc.stdout


def gh_api(path: str, *, gh: str = "gh", timeout: int = 90) -> object:
    """GET через `gh api`; отказ/битый JSON = GateError."""
    out = _run([gh, "api", path], timeout=timeout)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise GateError(f"битый JSON от gh api {path}: {exc}") from exc


def gh_json(args: Sequence[str], *, gh: str = "gh", timeout: int = 90) -> object:
    """`gh <args...>` с --json/--jq; отказ = GateError."""
    out = _run([gh, *args], timeout=timeout)
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise GateError(f"битый JSON от gh {' '.join(args[:2])}: {exc}") from exc


def load_required_checks(path: str | Path) -> list[str]:
    """Список required contexts из ci/required-checks.json (не может быть пуст)."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GateError(f"не удалось прочитать {p}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GateError(f"битый JSON в {p}: {exc}") from exc
    ctx = data.get("contexts") if isinstance(data, dict) else None
    if not isinstance(ctx, list) or not ctx or not all(isinstance(c, str) and c for c in ctx):
        raise GateError(f"{p}: contexts обязан быть непустым списком строк")
    return list(ctx)


def remote_branch_head(branch: str, repo: str, *, gh: str = "gh") -> str:
    """Голова ветки в origin через `gh api` (токен не попадает в git config).

    `matching-refs` используется вместо `git/ref`: имена веток candy-bot/*
    содержат слэши, а префиксный поиск + точное сравнение ref не даёт
    принять другую ветку за эту.
    """
    if not repo:
        raise GateError("repo обязателен для проверки головы ветки")
    data = gh_api(f"repos/{repo}/git/matching-refs/heads/{branch}", gh=gh)
    if not isinstance(data, list):
        raise GateError(f"неожиданный ответ matching-refs для {branch}")
    want = f"refs/heads/{branch}"
    matches = [r for r in data if isinstance(r, dict) and r.get("ref") == want]
    if len(matches) != 1:
        raise GateError(f"ветка {want} найдена {len(matches)} раз(а) — ожидалась ровно 1")
    obj = matches[0].get("object") or {}
    sha = obj.get("sha")
    if not isinstance(sha, str) or len(sha) < 7:
        raise GateError(f"подозрительный head у ветки {branch}: {sha!r}")
    return sha


def pr_head_sha(pr_url: str, *, gh: str = "gh") -> str:
    """headRefOid PR — та самая точная SHA, к которой привязаны checks."""
    data = gh_json(["pr", "view", pr_url, "--json", "headRefOid"], gh=gh)
    try:
        sha = data["headRefOid"]
    except (TypeError, KeyError) as exc:
        raise GateError(f"headRefOid не получен для {pr_url}") from exc
    if not isinstance(sha, str) or len(sha) < 7:
        raise GateError(f"подозрительный headRefOid у {pr_url}: {sha!r}")
    return sha


def pr_state(pr_url: str, *, gh: str = "gh") -> str:
    data = gh_json(["pr", "view", pr_url, "--json", "state"], gh=gh)
    try:
        state = data["state"]
    except (TypeError, KeyError) as exc:
        raise GateError(f"state не получен для {pr_url}") from exc
    if not isinstance(state, str) or not state:
        raise GateError(f"пустой state у {pr_url}: {state!r}")
    return state


def check_runs(repo: str, sha: str, *, gh: str = "gh") -> list[dict]:
    """Все check-runs, привязанные к exact SHA. Любой отказ = GateError."""
    data = gh_api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100", gh=gh)
    if not isinstance(data, dict):
        raise GateError(f"неожиданный ответ check-runs для {sha}")
    runs = data.get("check_runs")
    if not isinstance(runs, list):
        raise GateError(f"check-runs отсутствуют в ответе для {sha}")
    for run in runs:
        if not isinstance(run, dict):
            raise GateError("элемент check-runs не объект")
        got = run.get("head_sha")
        # Требование: каждый наблюдаемый check обязан принадлежать exact head SHA.
        if got != sha:
            raise GateError(
                f"check '{run.get('name')}' сообщает head_sha={got}, ожидался {sha}")
    return runs


def evaluate(runs: Iterable[dict], required: Sequence[str]) -> tuple[bool, list[str]]:
    """(все готовы?, отчёт по каждому required context)."""
    by_name: dict[str, list[dict]] = {}
    for run in runs:
        by_name.setdefault(str(run.get("name", "")), []).append(run)
    report: list[str] = []
    ready = True
    for ctx in required:
        candidates = by_name.get(ctx, [])
        if not candidates:
            report.append(f"{ctx}: MISSING (нет check-run на этом SHA)")
            ready = False
            continue
        conclusions = [c.get("conclusion") for c in candidates]
        if any(c in BAD_CONCLUSIONS for c in conclusions):
            report.append(f"{ctx}: FAILED ({conclusions})")
            ready = False
            continue
        if all(c in OK_CONCLUSIONS for c in conclusions):
            report.append(f"{ctx}: ok")
            continue
        report.append(f"{ctx}: PENDING ({conclusions})")
        ready = False
    return ready, report


def wait_for_checks(
    repo: str,
    sha: str,
    required: Sequence[str],
    *,
    timeout_min: float,
    gh: str = "gh",
    poll_s: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[str]:
    """Ждёт, пока все required checks не будут ok на exact SHA.

    Fail-closed: отказ API, «провалившийся» check, истёкший таймаут — GateError.
    """
    if not required:
        raise GateError("список required checks пуст")
    deadline = clock() + timeout_min * 60
    last_report: list[str] = []
    while True:
        runs = check_runs(repo, sha, gh=gh)
        ready, last_report = evaluate(runs, required)
        if ready:
            return last_report
        # Явный отказ required check — ждать бессмысленно.
        bad = [line for line in last_report if ": FAILED" in line]
        if bad:
            raise GateError("required check завершился отказом: " + "; ".join(bad))
        if clock() >= deadline:
            raise GateError(
                f"таймаут {timeout_min} мин: checks не сошлись на {sha}: "
                + "; ".join(last_report))
        sleep(poll_s)


def dispatch_workflow(
    workflow: str,
    *,
    branch: str,
    head_sha: str,
    repo: str,
    gh: str = "gh",
) -> None:
    """Явный dispatch validate.yml на точной bot-ветке.

    GITHUB_TOKEN-PR сам CI не триггерит, поэтому checks запускаются вручную.
    До и после dispatch голова ветки обязана быть ровно head SHA PR — иначе
    checks пошли бы не на тот SHA, что защищает ветка master.
    """
    if not branch.startswith("candy-bot/"):
        raise GateError(f"dispatch только для candy-bot/*, получено {branch!r}")
    if not head_sha:
        raise GateError("head SHA обязателен: dispatch обязан быть привязан к точному PR head")
    before = remote_branch_head(branch, repo, gh=gh)
    if before != head_sha:
        raise GateError(
            f"голова ветки {branch} ({before}) не совпала с head PR ({head_sha})")
    _run([gh, "workflow", "run", workflow, "--ref", branch], timeout=60)
    after = remote_branch_head(branch, repo, gh=gh)
    if after != head_sha:
        raise GateError(
            f"после dispatch голова ветки {branch} сменилась: {after} != {head_sha}")


def observe_merged(
    pr_url: str,
    *,
    timeout_min: float,
    gh: str = "gh",
    poll_s: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """Ждёт фактического verdict `merged`. closed-unmerged/таймаут = GateError."""
    deadline = clock() + timeout_min * 60
    while True:
        state = pr_state(pr_url, gh=gh)
        if state == "MERGED":
            return state
        if state != "OPEN":
            raise GateError(f"PR {pr_url} перешёл в {state} без merge")
        if clock() >= deadline:
            raise GateError(
                f"таймаут {timeout_min} мин: {pr_url} остался OPEN — merge не подтверждён")
        sleep(poll_s)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="merge_gate.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dispatch", help="явный dispatch workflow на bot-ветке")
    d.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    d.add_argument("--branch", required=True)
    d.add_argument("--head-sha", required=True,
                   help="headRefOid PR — dispatch обязан быть привязан к нему")
    d.add_argument("--repo", required=True)
    d.add_argument("--gh", default="gh")

    w = sub.add_parser("wait-checks", help="дождаться required checks на exact SHA")
    w.add_argument("--repo", required=True)
    w.add_argument("--head-sha", required=True)
    w.add_argument("--checks-file", default=DEFAULT_CHECKS_FILE)
    w.add_argument("--timeout-min", type=float, default=20.0)
    w.add_argument("--poll-s", type=float, default=15.0)
    w.add_argument("--gh", default="gh")

    o = sub.add_parser("observe-merged", help="дождаться фактического merged verdict")
    o.add_argument("--pr-url", required=True)
    o.add_argument("--timeout-min", type=float, default=20.0)
    o.add_argument("--poll-s", type=float, default=15.0)
    o.add_argument("--gh", default="gh")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.cmd == "dispatch":
            dispatch_workflow(
                args.workflow, branch=args.branch, head_sha=args.head_sha,
                repo=args.repo, gh=args.gh)
            print(f"dispatched {args.workflow} on {args.branch} @ {args.head_sha}")
        elif args.cmd == "wait-checks":
            required = load_required_checks(args.checks_file)
            report = wait_for_checks(
                args.repo, args.head_sha, required,
                timeout_min=args.timeout_min, poll_s=args.poll_s, gh=args.gh)
            print("required checks ok on " + args.head_sha)
            for line in report:
                print("  " + line)
        elif args.cmd == "observe-merged":
            state = observe_merged(
                args.pr_url, timeout_min=args.timeout_min,
                poll_s=args.poll_s, gh=args.gh)
            print(f"PR merged: {args.pr_url} ({state})")
        return 0
    except GateError as exc:
        print(f"::error::merge-gate: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
