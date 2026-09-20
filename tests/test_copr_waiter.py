import pytest

from copr_waiter import (
    ApiError,
    NONTERMINAL,
    TERMINAL,
    decide,
    format_summary,
    parse_build_ids,
    wait_for_build,
)

REQUIRED = [
    "opensuse-tumbleweed-x86_64",
    "opensuse-tumbleweed-aarch64",
    "opensuse-leap-16.0-x86_64",
    "opensuse-leap-16.0-aarch64",
]


def all_succeeded():
    return {c: "succeeded" for c in REQUIRED}


def step_instant(_n):
    return 0.0


def _finish(rc, seen, verdict):
    return rc, seen, verdict


class TestParseBuildIds:
    def test_created_builds_single(self):
        assert parse_build_ids("Created build(s) 123456.") == [123456]

    def test_created_builds_multiple(self):
        assert parse_build_ids("Created build(s) 123456,789012.") == [123456, 789012]

    def test_build_was_created_id(self):
        assert parse_build_ids("Build was created (id 987654).") == [987654]

    def test_created_builds_colon_plural(self):
        assert parse_build_ids("Created builds: 11005028") == [11005028]

    def test_created_builds_colon_multiple(self):
        assert parse_build_ids("Created builds: 11005028, 11005029") == [11005028, 11005029]

    def test_empty_on_garbage(self):
        assert parse_build_ids("nothing here at all") == []


class TestDecideOk:
    def test_4_of_4_succeeded(self):
        seen = {}
        concluded, verdict = decide(all_succeeded(), REQUIRED, 5, 100, seen)
        assert concluded is True
        assert verdict == "ok"

    def test_queued_running_then_succeeded(self):
        seq = [
            {REQUIRED[0]: "queued", REQUIRED[1]: "running"},
            {REQUIRED[2]: "running", REQUIRED[3]: "queued"},
            all_succeeded(),
        ]
        seen = {}
        for i, s in enumerate(seq):
            concluded, _ = decide(s, REQUIRED, 2 * (i + 1), 100, seen)
            if i < len(seq) - 1:
                assert concluded is False
        assert seen[REQUIRED[0]] == "succeeded"
        concluded, verdict = decide({}, REQUIRED, 10, 100, seen)
        assert concluded is True
        assert verdict == "ok"


class TestDecideFailures:
    def test_one_chroot_failed(self):
        poll = all_succeeded()
        poll[REQUIRED[1]] = "failed"
        seen = {}
        concluded, verdict = decide(poll, REQUIRED, 5, 100, seen)
        assert concluded is True
        assert "failed" in verdict

    def test_canceled(self):
        poll = all_succeeded()
        poll[REQUIRED[0]] = "canceled"
        seen = {}
        concluded, verdict = decide(poll, REQUIRED, 5, 100, seen)
        assert concluded is True
        assert "canceled" in verdict

    def test_skipped(self):
        poll = all_succeeded()
        poll[REQUIRED[3]] = "skipped"
        seen = {}
        concluded, verdict = decide(poll, REQUIRED, 5, 100, seen)
        assert concluded is True
        assert "skipped" in verdict

    def test_none_state_is_failure(self):
        poll = all_succeeded()
        poll[REQUIRED[2]] = "none"
        seen = {}
        concluded, verdict = decide(poll, REQUIRED, 5, 100, seen)
        assert concluded is True
        assert "none" in verdict

    def test_terminal_build_with_missing_chroot(self):
        poll = {REQUIRED[0]: "succeeded", REQUIRED[1]: "succeeded", REQUIRED[2]: "succeeded"}
        seen = {}
        concluded, verdict = decide(poll, REQUIRED, 5, 100, seen, parent_state="failed")
        assert concluded is True
        assert "missing" in seen[REQUIRED[3]]
        assert verdict != "ok"

    def test_missing_chroot_while_parent_nonterminal_keeps_polling(self):
        poll = {REQUIRED[0]: "succeeded", REQUIRED[1]: "succeeded", REQUIRED[2]: "succeeded"}
        seen = {}
        concluded, _ = decide(poll, REQUIRED, 5, 100, seen, parent_state="running")
        assert concluded is False

    def test_missing_chroot_with_parent_unknown_keeps_polling(self):
        poll = {REQUIRED[0]: "succeeded", REQUIRED[1]: "running"}
        seen = {}
        concluded, _ = decide(poll, REQUIRED, 10, 100, seen, parent_state=None)
        assert concluded is False

    def test_missing_chroot_before_deadline_is_nonterminal(self):
        poll = {REQUIRED[0]: "succeeded", REQUIRED[1]: "running"}
        seen = {}
        concluded, _ = decide(poll, REQUIRED, 10, 100, seen)
        assert concluded is False

    def test_unexpected_extra_chroot(self):
        poll = all_succeeded()
        poll["opensuse-tumbleweed-ppc64le"] = "succeeded"
        seen = {}
        concluded, verdict = decide(poll, REQUIRED, 5, 100, seen)
        assert concluded is True
        assert "unexpected chroot" in verdict

    def test_timeout(self):
        poll = {REQUIRED[0]: "succeeded"}
        seen = {}
        concluded, verdict = decide(poll, REQUIRED, 150, 100, seen)
        assert concluded is True
        assert "timeout" in verdict

    def test_unknown_state(self):
        poll = {REQUIRED[0]: "suspicious-state"}
        seen = {}
        concluded, verdict = decide(poll, REQUIRED, 5, 100, seen)
        assert concluded is True
        assert "neither terminal nor known-inflight" in verdict


class TestDecideInflight:
    def test_running_is_inflight(self):
        poll = {REQUIRED[0]: "running", REQUIRED[1]: "importing", REQUIRED[2]: "pending"}
        seen = {}
        concluded, _ = decide(poll, REQUIRED, 5, 100, seen)
        assert concluded is False


class TestWaitForBuild:
    def test_success_path(self):
        calls = []

        def fetch(_bid):
            if not calls:
                calls.append(1)
                return {REQUIRED[0]: "queued"}
            return all_succeeded()

        rc, seen, verdict = wait_for_build(42, REQUIRED, fetch, deadline=100, step=step_instant)
        assert rc == 0
        assert verdict == "ok"
        assert seen[REQUIRED[0]] == "succeeded"

    def test_failure_path(self):
        def fetch(_bid):
            poll = all_succeeded()
            poll[REQUIRED[2]] = "failed"
            return poll

        rc, seen, verdict = wait_for_build(42, REQUIRED, fetch, deadline=100, step=step_instant)
        assert rc == 1
        assert "failed" in verdict

    def test_api_error_propagates(self):
        def fetch(_bid):
            raise ApiError("COPR API HTTP 503")

        with pytest.raises(ApiError):
            wait_for_build(42, REQUIRED, fetch, deadline=100, step=step_instant)

    def test_transient_api_error_then_recovery(self):
        calls = []

        def fetch(_bid):
            calls.append(1)
            if len(calls) == 1:
                raise ApiError("COPR API HTTP 503")
            return all_succeeded()

        rc, seen, verdict = wait_for_build(42, REQUIRED, fetch, deadline=100, step=step_instant)
        assert rc == 0
        assert verdict == "ok"

    def test_timeout(self):
        def fetch(_bid):
            return {REQUIRED[0]: "running"}

        _, _, verdict = wait_for_build(42, REQUIRED, fetch, deadline=0.05, step=step_instant)
        assert "timeout" in verdict

    def test_eventual_appearance_of_missing_chroot(self):
        states = [
            {REQUIRED[0]: "succeeded", REQUIRED[1]: "succeeded", REQUIRED[2]: "succeeded"},
            all_succeeded(),
        ]
        parents = ["running", "succeeded"]
        polls = []

        def fetch(_bid):
            polls.append("chroot")
            return states[min(len(states) - 1, len(polls) - 1)]

        def fetch_parent(_bid):
            return parents[min(len(parents) - 1, len(polls) - 1)]

        rc, seen, verdict = wait_for_build(
            42, REQUIRED, fetch, deadline=100, step=step_instant, fetch_parent=fetch_parent,
        )
        assert rc == 0
        assert verdict == "ok"
        assert REQUIRED[3] in seen
        assert seen[REQUIRED[3]] == "succeeded"


class TestFormatSummary:
    def test_summary_contains_all_chroots(self):
        seen = {c: "succeeded" for c in REQUIRED}
        lines = format_summary(7, seen, REQUIRED, "ok")
        joined = "\n".join(lines)
        for c in REQUIRED:
            assert c in joined
        assert "succeeded" in joined
        assert "ok" in joined