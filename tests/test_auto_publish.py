"""test_auto_publish.py — unit tests for the auto-publish wave pipeline."""
import os

import pytest

from conftest import load_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def mod():
    return load_module("auto_publish", os.path.join(ROOT, "bin/auto-publish.py"))


class TestNeedsPilot:
    def test_never_built_needs_pilot(self, mod):
        assert mod.needs_pilot("foo", "1.0.0", []) is True

    def test_matching_success_does_not_need(self, mod):
        hist = [("succeeded", "1.0.0", 1)]
        assert mod.needs_pilot("foo", "1.0.0", hist) is False

    def test_newer_target_needs_pilot(self, mod):
        hist = [("succeeded", "1.0.0", 1)]
        assert mod.needs_pilot("foo", "1.5.0", hist) is True

    def test_failed_latest_with_old_success_needs_or_not(self, mod):
        hist = [("failed", "1.5.0", 2), ("succeeded", "1.0.0", 1)]
        # цель 1.0.0 достигнута зелёным в pilot => не нужно
        assert mod.needs_pilot("foo", "1.0.0", hist) is False
        # цель 1.5.0 ещё НЕ достигнута зелёным => нужно (ребuild для ретрей)
        assert mod.needs_pilot("foo", "1.5.0", hist) is True

    def test_version_prefix_matching(self, mod):
        hist = [("succeeded", "1.0.0-1.suse.tw", 1)]
        assert mod.needs_pilot("foo", "1.0.0", hist) is False


class TestActiveBuild:
    def test_no_active_when_all_terminal(self, mod):
        hist = [("succeeded", "1.0.0", 2), ("failed", "0.9.0", 1)]
        assert mod.active_build(hist) is None

    def test_newest_active_returned(self, mod):
        hist = [("running", "1.1.0", 9), ("queued", "1.0.0", 8), ("failed", "0.9.0", 1)]
        assert mod.active_build(hist) == 9

    def test_pending_importing_are_active(self, mod):
        for state in ("pending", "starting", "importing", "queued", "waiting"):
            assert mod.active_build([(state, "1.0.0", 42)]) == 42


class TestBuildPlan:
    def _pkg(self, name, **kw):
        base = {"name": name, "prio": 5, "enabled": True}
        base.update(kw)
        return base

    def test_disabled_and_versionless_skipped(self, mod):
        pkgs = [self._pkg("zenith", enabled=False), self._pkg("noversion", prio=1)]
        plan = mod.build_plan(pkgs, {}, {}, 10)
        assert plan == []

    def test_wave_size_limited(self, mod):
        pkgs = [self._pkg(f"p{i}", prio=i) for i in range(1, 9)]
        versions = {f"p{i}": "1.0.0" for i in range(1, 9)}
        plan = mod.build_plan(pkgs, versions, {}, 3)
        assert [p["name"] for p in plan] == ["p1", "p2", "p3"]

    def test_only_fresh_targets_selected(self, mod):
        pkgs = [self._pkg("fresh", prio=1), self._pkg("baked", prio=0)]
        versions = {"fresh": "2.0.0", "baked": "1.0.0"}
        hist = {"baked": [("succeeded", "1.0.0", 1)]}
        plan = mod.build_plan(pkgs, versions, hist, 10)
        assert [p["name"] for p in plan] == ["fresh"]

    def test_force_rebuilds_reached_targets(self, mod):
        pkgs = [self._pkg("baked", prio=0)]
        versions = {"baked": "1.0.0"}
        hist = {"baked": [("succeeded", "1.0.0", 1)]}
        without = mod.build_plan(pkgs, versions, hist, 10)
        assert without == []
        with_force = mod.build_plan(pkgs, versions, hist, 10, force=True)
        assert with_force and with_force[0]["force"] is True

    def test_failed_latest_target_selected(self, mod):
        pkgs = [self._pkg("broken", prio=1)]
        versions = {"broken": "1.5.0"}
        hist = {"broken": [("failed", "1.5.0", 2), ("succeeded", "1.0.0", 1)]}
        plan = mod.build_plan(pkgs, versions, hist, 10)
        assert [p["name"] for p in plan] == ["broken"]

    def test_active_pilot_build_carried_for_resume(self, mod):
        pkgs = [self._pkg("inflight", prio=1)]
        versions = {"inflight": "1.2.0"}
        hist = {"inflight": [("running", "1.2.0", 77), ("succeeded", "1.0.0", 1)]}
        plan = mod.build_plan(pkgs, versions, hist, 10)
        assert plan[0]["pilot_build"] == 77

    def test_max_wave_zero_rejected(self, mod):
        with pytest.raises(mod.WavePlanError):
            mod.build_plan([], {}, {}, 0)


class TestSummarize:
    def test_counts(self, mod):
        items = [{"outcome": "promoted"}, {"outcome": "promoted"},
                 {"outcome": "pilot-not-clean"}, {"outcome": "pipeline-error"}]
        counts = mod.summarize(items)
        assert counts["promoted"] == 2
        assert counts["pilot-not-clean"] == 1

    def test_nothing_but_success_and_skip_counts_as_success(self, mod):
        items = [{"outcome": "promoted"}, {"outcome": "srpm-skip"}]
        counts = mod.summarize(items)
        assert set(counts) == {"promoted", "srpm-skip"}


class TestRenderMarkdown:
    def test_table_has_every_item(self, mod):
        wave = {
            "fetched_at": 1,
            "items": [
                {"name": "linuxwave", "target": "0.4.0",
                 "pilot_build": "100", "pilot_verdict": "ok",
                 "stable_build": "101", "stable_verdict": "ok", "outcome": "promoted"},
                {"name": "jq", "target": "1.7.1",
                 "pilot_verdict": "fail", "outcome": "pilot-not-clean"},
            ],
            "counts": {"promoted": 1, "pilot-not-clean": 1},
            "errors": [],
        }
        md = mod.render_markdown(wave)
        assert "linuxwave" in md and "jq" in md
        assert "pilot-not-clean" in md
        assert md.startswith("### Волна")