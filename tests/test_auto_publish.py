"""test_auto_publish.py — unit tests for the auto-publish wave pipeline."""
import json
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

    def test_version_mismatch_is_not_resumable(self, mod):
        """P0: активная сборка ЧУЖОЙ версии не переиспользуется как наш target."""
        hist = [("running", "1.1.0", 99), ("queued", "1.0.0", 55)]
        assert mod.active_build(hist, "1.0.0") == 55
        assert mod.active_build(hist, "2.0.0") is None

    def test_release_suffix_treated_as_same_target(self, mod):
        assert mod.active_build([("running", "1.0.0-3.fc44", 7)], "1.0.0") == 7


class TestVerBase:
    def test_strips_release(self, mod):
        assert mod.ver_base("1.2.3-4.fc44") == "1.2.3"
        assert mod.ver_base("1.2.3") == "1.2.3"

    def test_target_reached_requires_green_same_version(self, mod):
        assert mod.target_reached("1.0.0", [("succeeded", "1.0.0-1", 1)]) is True
        assert mod.target_reached("1.0.0", [("succeeded", "1.1.0-1", 1)]) is False
        assert mod.target_reached("1.0.0", [("failed", "1.0.0-1", 1)]) is False
        assert mod.target_reached("1.0.0", []) is False


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
        stable = {"baked": [("succeeded", "1.0.0-1", 2)]}
        plan = mod.build_plan(pkgs, versions, hist, 10, stable_hist=stable)
        assert [p["name"] for p in plan] == ["fresh"]

    def test_force_rebuilds_reached_targets(self, mod):
        pkgs = [self._pkg("baked", prio=0)]
        versions = {"baked": "1.0.0"}
        pilot = {"baked": [("succeeded", "1.0.0", 1)]}
        stable = {"baked": [("succeeded", "1.0.0-1", 2)]}
        without = mod.build_plan(pkgs, versions, pilot, 10, stable_hist=stable)
        assert without == []
        with_force = mod.build_plan(pkgs, versions, pilot, 10, force=True,
                                    stable_hist=stable)
        assert with_force and with_force[0]["force"] is True

    def test_pilot_green_stable_missing_needs_work_without_force(self, mod):
        """Новая семантика: гэп в stable — это работа, а не «уже всё зелёное»."""
        pkgs = [self._pkg("gapped", prio=0)]
        versions = {"gapped": "1.0.0"}
        pilot = {"gapped": [("succeeded", "1.0.0", 1)]}
        plan = mod.build_plan(pkgs, versions, pilot, 10, stable_hist={})
        assert [p["name"] for p in plan] == ["gapped"]
        assert plan[0]["mode"] == "promote-only"

    def test_failed_latest_target_selected(self, mod):
        pkgs = [self._pkg("broken", prio=1)]
        versions = {"broken": "1.5.0"}
        hist = {"broken": [("failed", "1.5.0", 2), ("succeeded", "1.0.0", 1)]}
        plan = mod.build_plan(pkgs, versions, hist, 10, stable_hist={})
        assert [p["name"] for p in plan] == ["broken"]

    def test_active_pilot_build_carried_for_resume(self, mod):
        pkgs = [self._pkg("inflight", prio=1)]
        versions = {"inflight": "1.2.0"}
        hist = {"inflight": [("running", "1.2.0", 77), ("succeeded", "1.0.0", 1)]}
        plan = mod.build_plan(pkgs, versions, hist, 10, stable_hist={})
        assert plan[0]["pilot_build"] == 77

    def test_active_pilot_build_other_version_ignored(self, mod):
        pkgs = [self._pkg("inflight", prio=1)]
        versions = {"inflight": "1.2.0"}
        hist = {"inflight": [("running", "9.9.9", 77)]}
        plan = mod.build_plan(pkgs, versions, hist, 10, stable_hist={})
        assert plan[0]["pilot_build"] is None


class TestPlanKeepsStableGaps:
    """P1: пакет с зелёным pilot, но не-зелёным stable ОБЯЗАН попасть в волну."""

    def _pkg(self, name):
        return {"name": name, "prio": 1, "enabled": True}

    def _plan(self, mod, stable_hist):
        pkgs = [self._pkg("CrabFetch")]
        versions = {"CrabFetch": "0.5.4"}
        pilot = {"CrabFetch": [("succeeded", "0.5.4-1", 11031313)]}
        return mod.build_plan(pkgs, versions, pilot, 10, stable_hist={"CrabFetch": stable_hist})

    def test_stable_missing_kept(self, mod):
        plan = self._plan(mod, [])
        assert [p["name"] for p in plan] == ["CrabFetch"]
        assert plan[0]["mode"] == "promote-only"

    def test_stable_failed_kept(self, mod):
        plan = self._plan(mod, [("failed", "0.5.4-1", 11040000)])
        assert [p["name"] for p in plan] == ["CrabFetch"]
        assert plan[0]["mode"] == "promote-only"

    def test_stable_active_same_version_resumed(self, mod):
        plan = self._plan(mod, [("running", "0.5.4-1", 11041111)])
        assert plan[0]["stable_build"] == 11041111
        assert plan[0]["mode"] == "promote-only"

    def test_stable_active_other_version_not_resumed(self, mod):
        plan = self._plan(mod, [("running", "0.9.9-1", 11042222)])
        assert plan[0]["stable_build"] is None

    def test_successful_stable_target_not_rebuilt(self, mod):
        assert self._plan(mod, [("succeeded", "0.5.4-1", 11045000)]) == []

    def test_successful_stable_target_other_version_is_work(self, mod):
        plan = self._plan(mod, [("succeeded", "0.4.0-1", 11043000)])
        assert [p["name"] for p in plan] == ["CrabFetch"]


class TestCleanGate:
    """P0: только promoted — успех. srpm-skip/not-attempted — FAILURE."""

    def test_promoted_only_is_clean(self, mod):
        assert mod.wave_is_clean([{"name": "a", "outcome": "promoted"}]) is True

    def test_srpm_skip_is_failure(self, mod):
        items = [{"name": "a", "outcome": "srpm-skip"}]
        assert mod.wave_is_clean(items) is False
        assert [e["name"] for e in mod.failed_items(items)] == ["a"]

    def test_not_attempted_is_failure(self, mod):
        assert mod.wave_is_clean([{"name": "a", "outcome": "not-attempted"}]) is False

    @pytest.mark.parametrize("outcome", [
        "pilot-not-clean", "stable-not-clean", "pilot-submit-failure",
        "stable-submit-failure", "pipeline-error", "missing", "timeout", "unknown",
    ])
    def test_every_other_outcome_fails(self, mod, outcome):
        assert mod.wave_is_clean([{"name": "a", "outcome": outcome}]) is False

    def test_never_success_outcomes_are_all_failing(self, mod):
        for oc in mod.NEVER_SUCCESS_OUTCOMES:
            assert mod.wave_is_clean([{"name": "a", "outcome": oc}]) is False

    def test_empty_wave_is_clean(self, mod):
        assert mod.wave_is_clean([]) is True

    def test_collect_errors_sees_every_failure(self, mod):
        items = [
            {"name": "ok", "outcome": "promoted"},
            {"name": "skip", "outcome": "srpm-skip", "error": "SKIP upstream"},
            {"name": "pipe", "outcome": "pipeline-error", "error": "boom"},
        ]
        errs = mod.collect_errors(items)
        assert len(errs) == 2
        assert any("skip" in e and "SKIP upstream" in e for e in errs)
        assert any("pipe" in e for e in errs)


class TestSrpmVersionGate:
    def test_version_from_filename(self, mod):
        assert mod.srpm_base_version("/x/CrabFetch-0.5.4-1.fc44.src.rpm", "CrabFetch") == "0.5.4"
        assert mod.srpm_base_version("/x/tetro-tui-3.6.2-1.fc44.src.rpm", "tetro-tui") == "3.6.2"

    def test_make_srpm_passes_exact_target(self, mod, tmp_path, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            seen.setdefault("calls", []).append(list(cmd))
            if cmd[0] == "bin/make-srpm.sh":
                (tmp_path / "SRPMS").mkdir(parents=True, exist_ok=True)
                (tmp_path / "SRPMS" / "foo-9.9.9-1.fc44.src.rpm").write_bytes(b"rpm")
                return type("R", (), {"returncode": 0})()
            # rpm -qp: версия читается из заголовка
            return type("R", (), {"returncode": 0, "stdout": "9.9.9\n"})()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        path = mod.make_srpm("foo", "9.9.9", tmp_path / "logs")
        assert ["bin/make-srpm.sh", "foo", "9.9.9"] in seen["calls"]
        assert path.endswith("foo-9.9.9-1.fc44.src.rpm")

    def test_make_srpm_blocks_version_mismatch(self, mod, tmp_path, monkeypatch):
        def fake_run(cmd, **kw):
            if cmd[0] == "bin/make-srpm.sh":
                (tmp_path / "SRPMS").mkdir(parents=True, exist_ok=True)
                (tmp_path / "SRPMS" / "foo-1.0.0-1.fc44.src.rpm").write_bytes(b"rpm")
                return type("R", (), {"returncode": 0})()
            return type("R", (), {"returncode": 0, "stdout": "1.0.0\n"})()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        with pytest.raises(mod.WaveError, match="версии"):
            mod.make_srpm("foo", "9.9.9", tmp_path / "logs")

    def test_make_srpm_skip_returns_empty_not_success(self, mod, tmp_path, monkeypatch):
        def fake_run(cmd, **kw):
            log = tmp_path / "logs" / "srpm-foo.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("[SKIP] foo: апстрим-версия недоступна\n")
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        assert mod.make_srpm("foo", "1.0.0", tmp_path / "logs") == ""


class TestProcessPackage:
    CHROOTS = ["tw-x86_64", "tw-aarch64", "leap-x86_64", "leap-aarch64"]

    def _pkg(self, **kw):
        base = {"name": "CrabFetch", "target": "0.5.4", "pilot_build": None,
                "stable_build": None, "pilot_ok": True, "stable_ok": False,
                "mode": "promote-only", "force": False}
        base.update(kw)
        return base

    def test_promote_only_reuses_pilot_srpm_skips_pilot(self, mod, tmp_path,
                                                              monkeypatch):
        """pilot-цель зелёная => pilot НЕ пересобирается, stable получает тот же SRPM."""
        calls = {}

        def fake_fetch(name, target, builds, dest_dir):
            calls["fetch"] = (name, target)
            p = dest_dir / "CrabFetch-0.5.4-1.fc44.src.rpm"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"srpm-bytes")
            return str(p), "deadbeef", 11031313

        def fake_submit(project, srpm, conf):
            calls["submit"] = project
            return "11042000"

        def fake_wait(bid, chroots, token, timeout_min, interval):
            calls["waited"] = bid
            return 0, {c: "succeeded" for c in chroots}, "4/4 ok"

        monkeypatch.setattr(mod, "fetch_pilot_srpm", fake_fetch)
        monkeypatch.setattr(mod, "submit", fake_submit)
        monkeypatch.setattr(mod, "wait_exact_4_4", fake_wait)

        res = mod.process_package(self._pkg(), self.CHROOTS, None, None,
                                  tmp_path / "logs", 1.0, {}, [])
        assert res["outcome"] == "promoted"
        assert calls["submit"] == mod.STABLE          # только stable
        assert calls["waited"] == 11042000             # ждём stable-сборку
        assert res["srpm_source"] == "pilot-build:11031313"
        assert res["srpm_sha256"] == "deadbeef"

    def test_promote_only_without_pilot_artifacts_is_not_attempted(self, mod, tmp_path,
                                                                    monkeypatch):
        def boom(*a, **kw):
            raise mod.WaveError("pilot SRPM недоступен")

        monkeypatch.setattr(mod, "fetch_pilot_srpm", boom)
        res = mod.process_package(self._pkg(), self.CHROOTS, None, None,
                                  tmp_path / "logs", 1.0, {}, [])
        assert res["outcome"] == "not-attempted"
        assert res["error"]

    def test_srpm_skip_outcome_is_reported_with_error(self, mod, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "make_srpm", lambda *a, **kw: "")
        res = mod.process_package(self._pkg(mode="full"), self.CHROOTS, None, None,
                                  tmp_path / "logs", 1.0, {}, [])
        assert res["outcome"] == "srpm-skip"
        assert res["error"]
        assert mod.wave_is_clean([res]) is False

    def test_full_mode_waits_pilot_before_stable(self, mod, tmp_path, monkeypatch):
        order = []

        def fake_make(name, target, log_dir):
            p = tmp_path / "SRPMS" / f"{name}-{target}-1.fc44.src.rpm"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
            return str(p)

        def fake_submit(project, srpm, conf):
            order.append(f"submit:{project}")
            return "111"

        def fake_wait(bid, chroots, token, timeout_min, interval):
            order.append(f"wait:{bid}")
            return 0, {c: "succeeded" for c in chroots}, "4/4 ok"

        monkeypatch.setattr(mod, "make_srpm", fake_make)
        monkeypatch.setattr(mod, "submit", fake_submit)
        monkeypatch.setattr(mod, "wait_exact_4_4", fake_wait)
        res = mod.process_package(self._pkg(mode="full", pilot_ok=False),
                                  self.CHROOTS, None, None, tmp_path / "logs",
                                  1.0, {}, [])
        assert res["outcome"] == "promoted"
        assert order == [f"submit:{mod.PILOT}", "wait:111",
                         f"submit:{mod.STABLE}", "wait:111"]

    def test_pilot_not_clean_never_touches_stable(self, mod, tmp_path, monkeypatch):
        def fake_make(*a, **kw):
            p = tmp_path / "SRPMS" / "x-1.fc44.src.rpm"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
            return str(p)

        monkeypatch.setattr(mod, "make_srpm", fake_make)
        monkeypatch.setattr(mod, "submit", lambda *a, **kw: "777")
        monkeypatch.setattr(mod, "wait_exact_4_4",
                            lambda *a, **kw: (1, {}, "3/4 fail"))
        res = mod.process_package(self._pkg(mode="full", pilot_ok=False),
                                  self.CHROOTS, None, None, tmp_path / "logs",
                                  1.0, {}, [])
        assert res["outcome"] == "pilot-not-clean"
        assert res.get("stable_submitted") is None
        assert not res.get("stable_build")

    def test_max_wave_zero_rejected(self, mod):
        with pytest.raises(mod.WavePlanError):
            mod.build_plan([], {}, {}, 0)


class TestPublishedGroundTruth:
    """build-list COPR неполон: план обязан опираться на repodata."""

    def _pkg(self, name, **kw):
        base = {"name": name, "prio": 5, "enabled": True}
        base.update(kw)
        return base

    def test_package_in_repodata_is_not_replanned(self, mod):
        """Регрессия live-инцидента: 5 уже опубликованных пакетов попадали в план."""
        pkgs = [self._pkg("bandwhich", prio=i) for i in range(5)]
        versions = {"bandwhich": "0.23.1"}
        stable_pub = {"bandwhich": {"0.23.1"}}
        pilot_pub = {"bandwhich": {"0.23.1"}}
        plan = mod.build_plan(pkgs, versions, {}, 5, stable_hist={},
                              pilot_published=pilot_pub,
                              stable_published=stable_pub)
        assert plan == []

    def test_repodata_closes_gap_without_build_list_success(self, mod):
        """Зелёной записи в build-list нет, но версия опубликована — работа не нужна."""
        pkgs = [self._pkg("csview")]
        versions = {"csview": "1.3.4"}
        plan = mod.build_plan(pkgs, versions, {}, 5, stable_hist={},
                              pilot_published={"csview": {"1.3.4"}},
                              stable_published={"csview": {"1.3.4"}})
        assert plan == []

    def test_stable_gap_still_planned_even_if_pilot_published(self, mod):
        pkgs = [self._pkg("pybonsai")]
        versions = {"pybonsai": "1.0.0"}
        plan = mod.build_plan(pkgs, versions, {}, 5, stable_hist={},
                              pilot_published={"pybonsai": {"1.0.0"}},
                              stable_published={})
        assert [p["name"] for p in plan] == ["pybonsai"]
        assert plan[0]["mode"] == "promote-only"

    def test_older_target_is_dropped_as_downgrade(self, mod):
        """gum: в stable уже 2.0.2, а цель из state.json 2.0.1."""
        pkgs = [self._pkg("gum")]
        versions = {"gum": "2.0.1"}
        plan = mod.build_plan(pkgs, versions, {}, 5, stable_hist={},
                              pilot_published={"gum": {"2.0.1"}},
                              stable_published={"gum": {"2.0.1", "2.0.2"}})
        assert plan == []

    def test_force_does_not_override_downgrade_guard(self, mod):
        pkgs = [self._pkg("gum")]
        versions = {"gum": "2.0.1"}
        plan = mod.build_plan(pkgs, versions, {}, 5, force=True, stable_hist={},
                              pilot_published={"gum": {"2.0.1"}},
                              stable_published={"gum": {"2.0.2"}})
        assert plan == []

    def test_stale_targets_reported(self, mod):
        pkgs = [self._pkg("gum"), self._pkg("fine")]
        versions = {"gum": "2.0.1", "fine": "1.0.0"}
        stale = mod.stale_targets(pkgs, versions, {"gum": {"2.0.1"}},
                                  {"gum": {"2.0.2"}, "fine": {"1.0.0"}})
        assert [i["name"] for i in stale] == ["gum"]
        assert stale[0]["project"] == "stable"

    def test_without_repodata_behaviour_unchanged(self, mod):
        """Без repodata (None) семантика прежняя — пустые таблицы не ломают план."""
        pkgs = [self._pkg("fresh"), self._pkg("baked")]
        versions = {"fresh": "2.0.0", "baked": "1.0.0"}
        hist = {"baked": [("succeeded", "1.0.0", 1)]}
        stable = {"baked": [("succeeded", "1.0.0-1", 2)]}
        plan = mod.build_plan(pkgs, versions, hist, 10, stable_hist=stable)
        assert [p["name"] for p in plan] == ["fresh"]


class TestProbeFailClosed:
    def test_probe_error_becomes_plan_error(self, mod, monkeypatch):
        def boom(*a, **kw):
            raise mod.PublishProbeError("404")
        monkeypatch.setattr(mod, "fetch_published", boom)
        with pytest.raises(mod.WavePlanError) as exc:
            mod.probe_published(mod.STABLE, ["opensuse-tumbleweed-x86_64"])
        assert "repodata" in str(exc.value)

    def test_probe_error_stops_run_before_any_build(self, mod, monkeypatch, tmp_path,
                                                    capfd):
        def boom(*a, **kw):
            raise mod.PublishProbeError("404")
        monkeypatch.setattr(mod, "fetch_published", boom)

        def explode(*a, **kw):  # submit не должен вызываться вообще
            raise AssertionError("submit нельзя вызывать при недоступной repodata")
        monkeypatch.setattr(mod, "process_package", explode)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pkgs.json").write_text(json.dumps({
            "project": {"chroots": ["opensuse-tumbleweed-x86_64"]},
            "packages": [{"name": "x", "prio": 5, "enabled": True}],
        }))
        (tmp_path / "state").mkdir()
        (tmp_path / "state/state.json").write_text(json.dumps({"x": {"ver": "1.0"}}))
        monkeypatch.setattr(mod, "fetch_builds", lambda *a, **kw: [])
        assert mod.main(["run", "--max-wave", "1"]) == 1
        assert "PUBLISH-PROBE-FAILED" in capfd.readouterr().err


class TestProcessPackageDowngradeGuard:
    def test_downgrade_blocked_before_srpm_work(self, mod, tmp_path):
        out = mod.process_package(
            {"name": "gum", "target": "2.0.1", "mode": "full"},
            ["opensuse-tumbleweed-x86_64"], None, None, tmp_path, 0.1, {}, [],
            pilot_published={"gum": {"2.0.1"}},
            stable_published={"gum": {"2.0.1", "2.0.2"}},
        )
        assert out["outcome"] == "downgrade-blocked"
        assert mod.failed_items([out])  # НЕ тривиально «не помешало run»
        assert "state.json" in out["error"]


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


class TestArgParsing:
    def test_flags_after_subcommand(self, mod):
        for argv in (["plan", "--max-wave", "5"],
                     ["--max-wave", "5", "plan"],
                     ["run", "--max-wave", "5", "--confirm"],
                     ["run", "--confirm", "--max-wave", "5"]):
            args = mod.parse_args(mod.canonicalize(argv))
            assert args.command in ("plan", "run")
            assert args.max_wave == 5
        assert mod.parse_args(mod.canonicalize(["run", "--confirm"])).force is False
        assert mod.parse_args(mod.canonicalize(["run", "--confirm", "--force"])).force is True

    def test_force_requires_confirm(self, mod, capfd):
        assert mod.main(["run", "--force"]) == 1
        err = capfd.readouterr().err
        assert "--confirm" in err