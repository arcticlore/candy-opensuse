#!/usr/bin/env python3
"""COPR pilot build waiter — deterministic, testable state machine.

Pure-stdlib module. No third-party deps. Designed to:
  - submit via copr-cli in the workflow (this module ONLY observes);
  - watch one build across all declared chroots from pkgs.json;
  - succeed only on 4/4 ''succeeded'';
  - never exit 0 on failed/canceled/skipped/none/missing/unknown/API error/timeout;
  - support resume by existing build id (no duplicate submit).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

TERMINAL = {"succeeded", "failed", "canceled", "skipped"}
NONTERMINAL = {"pending", "starting", "importing", "running", "queued", "waiting"}
FINISHED_OK = "succeeded"


class ApiError(Exception):
    """Transient or persistent failure talking to the COPR API."""


def parse_build_ids(output_text: str) -> List[int]:
    """Extract numeric build ids from copr-cli output.

    Handles the common shapes copr-cli prints for --nowait submits:
      'Created build(s) 123456.'
      'Created build(s) 123456,789012.'
      'Build was created (id 123456).'
      'Created build 123456'
      'Created builds: 12005028'   (modern copr-cli, colon, plural)
    Returns [] if nothing parseable (caller must fail — silent 'success' is a bug).
    """
    import re

    ids: List[int] = []
    for m in re.finditer(
        r"(?:Created build[s]?\(s\)|Created builds?:|\bids?\b)[^0-9]*([0-9][0-9,\s]*)",
        output_text,
    ):
        for num in re.findall(r"[0-9]+", m.group(1)):
            if int(num) > 0:
                ids.append(int(num))
    return ids


def fetch_chroot_states(
    build_id: int,
    api_base: str,
    token: Optional[str],
    timeout: int = 30,
    max_retries: int = 3,
    retry_backoff: float = 5.0,
) -> Dict[str, str]:
    """Return {chroot: state} for a build via COPR APIv3, with retries.

    Raises ApiError if the API is unreachable after retries or returns
    non-JSON / HTTP error.
    """
    import urllib.error
    import urllib.request

    url = f"{api_base}/api_3/build-chroot/list?build_id={build_id}&limit=100"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)

    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read().decode("utf-8")
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ApiError(f"malformed JSON from COPR API: {exc}") from exc
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise ApiError(f"unexpected API payload shape: {type(data).__name__}")
            states = {}
            for item in items:
                name = item.get("name")
                state = item.get("state")
                if isinstance(name, str) and isinstance(state, str):
                    states[name] = state
            return states
        except urllib.error.HTTPError as exc:
            if attempt < max_retries and exc.code in (502, 503, 504):
                last_err = exc
                time.sleep(retry_backoff * (2 ** attempt))
                continue
            raise ApiError(f"COPR API HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            if attempt < max_retries:
                last_err = exc
                time.sleep(retry_backoff * (2 ** attempt))
                continue
            raise ApiError(f"COPR API unreachable: {exc.reason}") from exc
    raise ApiError(f"COPR API failed after retries: {last_err}")


def fetch_parent_state(
    build_id: int,
    api_base: str,
    token: Optional[str],
    timeout: int = 30,
    max_retries: int = 3,
    retry_backoff: float = 5.0,
) -> Optional[str]:
    """Return the parent COPR build's state (''succeeded'', ''running'', ...).

    The parent build is the authoritative ''is the whole thing done'' flags.
    Returns None if the parent state is not available (caller must treat a
    missing chroot as non-terminal while parent state is unknown).
    """
    import urllib.error
    import urllib.request

    url = f"{api_base}/api_3/build/{build_id}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)

    last_err: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read().decode("utf-8")
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ApiError(f"malformed JSON from parent-build API: {exc}") from exc
            if not isinstance(data, dict):
                raise ApiError(f"unexpected parent API payload shape: {type(data).__name__}")
            state = data.get("state")
            return state if isinstance(state, str) else None
        except urllib.error.HTTPError as exc:
            if attempt < max_retries and exc.code in (502, 503, 504):
                last_err = exc
                time.sleep(retry_backoff * (2 ** attempt))
                continue
            raise ApiError(f"parent-build COPR API HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            if attempt < max_retries:
                last_err = exc
                time.sleep(retry_backoff * (2 ** attempt))
                continue
            raise ApiError(f"parent-build COPR API unreachable: {exc.reason}") from exc
    raise ApiError(f"parent-build COPR API failed after retries: {last_err}")


def _statuses_from_rows(rows: List[Tuple[str, str, Optional[str]]]) -> Dict[str, str]:
    return {name: state for name, state, _url in rows}


def decide(
    poll: Dict[str, str],
    required_chroots: List[str],
    elapsed: float,
    deadline: float,
    seen: Optional[Dict[str, str]] = None,
    parent_state: Optional[str] = None,
) -> Tuple[bool, str]:
    """One decision step of the state machine.

    poll      — current {chroot: state} as reported this cycle.
    required  — canonical chroots this build must cover (from pkgs.json).
    elapsed   — seconds elapsed since submit.
    deadline  — seconds of total wall time allowed.
    seen      — running accumulator of last known state per chroot (mutated!).
    parent_state — authoritative parent COPR build state. A required chroot
        that has not appeared yet counts as terminal failure ONLY when the
        parent build is itself terminal; while the parent is non-terminal a
        missing chroot keeps the poll going (it may still appear).

    Returns (concluded, verdict). concluded=True means the caller must stop
    and treat 'verdict' as final. verdict is either 'ok' (4/4 succeeded) or a
    human-readable failure reason.
    """
    if seen is None:
        seen = {}
    for name, state in poll.items():
        seen[name] = state

    if elapsed >= deadline:
        _finish_accumulator(seen, required_chroots)
        return True, "timeout: deadline reached before terminal 4/4"

    unexpected = sorted(set(seen) - set(required_chroots))
    if unexpected:
        _finish_accumulator(seen, required_chroots)
        return True, f"unexpected chroot(s) observed: {', '.join(unexpected)}"

    for chroot in list(required_chroots):
        state = seen.get(chroot)
        if state is None:
            continue
        if state not in TERMINAL and state not in NONTERMINAL:
            _finish_accumulator(seen, required_chroots)
            return True, f"state '{state}' for {chroot} is neither terminal nor known-inflight"

    observed = [c for c in required_chroots if c in seen]
    missing = [c for c in required_chroots if c not in seen]

    if missing and parent_state not in TERMINAL:
        # Chroot ещё не появился, а родительский build не готов — продолжаем polling.
        return False, ""

    if missing:
        # Parent build уже terminal, но obligatory chroot так и не пришёл → failure.
        _finish_accumulator(seen, required_chroots)
        return True, f"terminal build missing chroot(s): {', '.join(missing)}"

    if observed and all(seen[c] in TERMINAL for c in observed):
        _finish_accumulator(seen, required_chroots)
        bad = [c for c in required_chroots if seen[c] != FINISHED_OK]
        bad_detail = ", ".join(f"{c} ({seen[c]})" for c in bad)
        if not bad:
            return True, "ok"
        return True, f"terminal non-succeeded chroots: {bad_detail}"

    return False, ""


def _finish_accumulator(seen: Dict[str, str], required_chroots: List[str]) -> None:
    for chroot in required_chroots:
        if chroot not in seen:
            seen[chroot] = "missing"


def format_summary(
    build_id: int,
    seen: Dict[str, str],
    required_chroots: List[str],
    verdict: str,
) -> List[str]:
    lines = [
        f"### COPR pilot build #{build_id}",
        "",
        "| chroot | state |",
        "| ------ | ----- |",
    ]
    for chroot in required_chroots:
        lines.append(f"| {chroot} | {seen.get(chroot, 'missing')} |")
    lines.append("")
    lines.append(f"**verdict:** {verdict}")
    return lines


def wait_for_build(
    build_id: int,
    required_chroots: List[str],
    fetch: Callable[[int], Dict[str, str]],
    deadline: float,
    step: Callable[[int], float],
    on_progress: Optional[Callable[[int, Dict[str, str]], None]] = None,
    fetch_parent: Optional[Callable[[int], Optional[str]]] = None,
    api_retries: int = 3,
) -> Tuple[int, Dict[str, str], str]:
    """Poll until conclusion. Returns (exit_code, last_seen, verdict).

    Transient ApiError from ``fetch``/``fetch_parent`` is retried up to
    ``api_retries`` times (bounded backoff); persistent failure raises
    (caller converts to exit 1).

    ``fetch_parent`` returns the authoritative parent build state; used to
    decide whether a not-yet-appeared required chroot is terminal-failing
    (parent terminal) or still in-flight (parent non-terminal).
    """
    start = time.monotonic()
    seen: Dict[str, str] = {}
    step_no = 0
    errors = 0
    while True:
        step_no += 1
        try:
            state = fetch(build_id)
            parent_state = fetch_parent(build_id) if fetch_parent else None
        except ApiError:
            errors += 1
            elapsed = time.monotonic() - start
            if errors > api_retries or elapsed >= deadline:
                raise
            time.sleep(step(step_no))
            continue
        errors = 0
        if on_progress:
            on_progress(step_no, state)
        elapsed = time.monotonic() - start
        concluded, verdict = decide(
            state, required_chroots, elapsed, deadline, seen, parent_state
        )
        if concluded:
            return (0 if verdict == "ok" else 1, seen, verdict)
        time.sleep(step(step_no))


def _read_required_chroots(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    chroots = data.get("project", {}).get("chroots")
    if not isinstance(chroots, list) or not chroots:
        raise ValueError(f"{path}: project.chroots missing or empty")
    return list(chroots)


def _write_step_summary(lines: List[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _cmd_print_build_ids(log_path: str) -> int:
    with open(log_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    ids = parse_build_ids(text)
    if not ids:
        print("ERROR: no numeric build id in output", file=sys.stderr)
        return 1
    print(ids[0])
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="COPR pilot build waiter (watch or parse build ids).")
    sub = parser.add_subparsers(dest="command", required=False)

    print_ids = sub.add_parser("print-build-ids", help="Extract numeric build id from a copr-cli log file")
    print_ids.add_argument("log_file", help="path to copr-cli submit output log")
    print_ids.set_defaults(handler=lambda a: _cmd_print_build_ids(a.log_file))

    watch = sub.add_parser("watch", help="Watch a build until terminal 4/4 (default)")
    watch.add_argument("--build-id", type=int, required=True, help="COPR build id (from submit or resume)")
    watch.add_argument("--pkgs-json", default="pkgs.json", help="path to pkgs.json (canonical chroots)")
    watch.add_argument("--api-base", default=os.environ.get("COPR_API_BASE", "https://copr.fedorainfracloud.org"))
    watch.add_argument("--token", default=os.environ.get("COPR_API_TOKEN", ""), help="COPR API bearer token (never printed)")
    watch.add_argument("--timeout-min", type=float, default=120.0, help="total deadline in minutes")
    watch.add_argument("--interval", type=float, default=30.0, help="base poll interval in seconds (bounded exponential backoff)")
    watch.add_argument("--no-parent", action="store_true", help=argparse.SUPPRESS)
    watch.set_defaults(handler="-watch")

    args = parser.parse_args(argv)

    if args.command == "print-build-ids":
        return args.handler(args)

    if args.command is None:
        parser.error("no subcommand (use 'watch' or 'print-build-ids')")

    required_chroots = _read_required_chroots(args.pkgs_json)
    deadline = args.timeout_min * 60.0

    def bounded_backoff(n: int) -> float:
        return min(args.interval * (2 ** (n - 1)), 3600.0)

    def fetch(build_id: int) -> Dict[str, str]:
        return fetch_chroot_states(build_id, args.api_base, args.token or None)

    def fetch_parent(build_id: int) -> Optional[str]:
        return fetch_parent_state(build_id, args.api_base, args.token or None)

    def progress(step_no: int, state: Dict[str, str]) -> None:
        print(f"  [poll {step_no}] {json.dumps(state, sort_keys=True)}", flush=True)

    try:
        rc, seen, verdict = wait_for_build(
            args.build_id,
            required_chroots,
            fetch,
            deadline,
            bounded_backoff,
            progress,
            fetch_parent if not args.no_parent else None,
        )
    except ApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        _write_step_summary(
            format_summary(args.build_id, {}, required_chroots, f"API error: {exc}")
        )
        return 1

    summary = format_summary(args.build_id, seen, required_chroots, verdict)
    _write_step_summary(summary)
    print("\n".join(summary), flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())