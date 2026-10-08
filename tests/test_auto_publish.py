"""test_auto_publish.py — unit tests for the auto-publish wave pipeline."""
import json
import hashlib
import os

import pytest

from conftest import load_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def mod():
    return load_module("auto_publish", os.path.join(ROOT, "bin/auto-publish.py"))


def fake_pilot_srpm(monkeypatch, mod, payload=b"exact-pilot-srpm", default_id=11064066):
    """Подмена fetch_pilot_srpm с фиксированным артефактом.

    Возвращает dict, куда пишется переданный build_id — тест проверяет, что
    частичный/remediation-путь идёт за SRPM ИМЕННО в ту сборку, что в плане.
    """
    import hashlib
    seen = {}

    def _fake(name, target, builds, dest_dir, build_id=None):
        seen["build_id"] = build_id
        assert build_id is not None, (
            "fetch обязан получать ТОЧНЫЙ pilot_srpm_build — "
            "succeeded-fallback удалён")
        dest = dest_dir / f"{name}-{target}.src.rpm"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return str(dest), hashlib.sha256(payload).hexdigest(), default_id

    monkeypatch.setattr(mod, "fetch_pilot_srpm", _fake)
    return seen


def self_chroots():
    return ["tw-x86_64", "tw-aarch64", "leap-x86_64", "leap-aarch64"]


def assert_srpm_bytes(path, payload=b"exact-pilot-srpm"):
    with open(path, "rb") as fh:
        assert fh.read() == payload, "SRPM обязан быть байт-в-байт как в pilot"


def probe_stub(mod, pilot_per=None, stable_per=None):
    """probe_all, который не ходит в сеть: состояние публикации задано явно.

    Тест обязан решить, что опубликовано, а не подгружать реальный проект —
    иначе падение сети в тесте превратилось бы в смену поведения пайплайна.
    """
    pilot_per = pilot_per or {}
    stable_per = stable_per or {}

    def _probe(project, chroots):
        per = pilot_per if project == mod.PILOT else stable_per
        # гарантируем присутствие всех запрошенных chroot'ов
        per = {c: per.get(c, {}) for c in chroots}
        union = {}
        for table in per.values():
            for name, versions in table.items():
                union.setdefault(name, set()).update(versions)
        return {"union": union, "per_chroot": per,
                "completed": mod.completed_table(per)}

    return _probe


@pytest.fixture(autouse=True)
def _no_live_repodata(mod, monkeypatch):
    """Ни один unit-тест не ходит в сеть за repodata.

    probe_all — единственный источник чтения репозитория; тест, не задавший
    состояние явно (probe_stub), получает ПУСТОЙ репозиторий, а не загрузку
    реального проекта. Состояние публикации — это утверждение теста, а не
    побочный эффект от наличия сети.
    """
    if not hasattr(mod, "_real_probe_all"):
        mod._real_probe_all = mod.probe_all   # оригинал до подмены
    monkeypatch.setattr(mod, "probe_all", probe_stub(mod))


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
        """Уже опубликованные в repodata пропускаются, остальные берутся в волну."""
        pkgs = [self._pkg("fresh", prio=1), self._pkg("baked", prio=0)]
        versions = {"fresh": "2.0.0", "baked": "1.0.0"}
        hist = {"baked": [("succeeded", "1.0.0", 1)]}
        stable = {"baked": [("succeeded", "1.0.0-1", 2)]}
        done = {"baked": {"1.0.0"}}
        plan = mod.build_plan(pkgs, versions, hist, 10, stable_hist=stable,
                              pilot_published=dict(done, **{"fresh": set()}),
                              stable_published=dict(done, **{"fresh": set()}),
                              pilot_completed=dict(done, **{"fresh": set()}),
                              stable_completed=dict(done, **{"fresh": set()}))
        assert [p["name"] for p in plan] == ["fresh"]

    def test_green_build_alone_never_closes_goal(self, mod):
        """P0 #2: зелёная запись build-list НЕ закрывает цель без repodata.

        История сборок неполна (у 133 из 137 опубликованных пакетов записи
        `succeeded` нет вовсе) и ничего не говорит о том, в каких chroot версия
        появилась, поэтому основанием закрытия может быть только пересечение
        repodata.
        """
        pkgs = [self._pkg("baked", prio=0)]
        versions = {"baked": "1.0.0"}
        hist = {"baked": [("succeeded", "1.0.0", 1)]}
        stable = {"baked": [("succeeded", "1.0.0-1", 2)]}
        plan = mod.build_plan(pkgs, versions, hist, 10, stable_hist=stable)
        assert [p["name"] for p in plan] == ["baked"], (
            "build-list без repodata не должен считаться «цель достигнута»")

    def test_force_rebuilds_reached_targets(self, mod):
        pkgs = [self._pkg("baked", prio=0)]
        versions = {"baked": "1.0.0"}
        pilot = {"baked": [("succeeded", "1.0.0", 1)]}
        stable = {"baked": [("succeeded", "1.0.0-1", 2)]}
        pub = {"baked": {"1.0.0"}}
        kw = dict(pilot_published=pub, stable_published=pub,
                  pilot_completed=pub, stable_completed=pub)
        without = mod.build_plan(pkgs, versions, pilot, 10, stable_hist=stable, **kw)
        assert without == []
        with_force = mod.build_plan(pkgs, versions, pilot, 10, force=True,
                                    stable_hist=stable, **kw)
        assert with_force and with_force[0]["force"] is True

    def test_pilot_green_stable_missing_needs_work_without_force(self, mod):
        """Новая семантика: гэп в stable — это работа, а не «уже всё зелёное»."""
        pkgs = [self._pkg("gapped", prio=0)]
        versions = {"gapped": "1.0.0"}
        pilot = {"gapped": [("succeeded", "1.0.0", 1)]}
        done = {"gapped": {"1.0.0"}}
        plan = mod.build_plan(pkgs, versions, pilot, 10, stable_hist={},
                              pilot_published=done, pilot_completed=done)
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
    """P1: пакет с закрытым pilot, но не закрытым stable ОБЯЗАН попасть в волну.

    «Закрыт» здесь значит только одно: версия опубликована во ВСЕХ chroot по
    repodata. История сборок в плане участвует исключительно как источник
    build id для resume (active_build) и никогда — как признак завершения.
    """

    def _pkg(self, name):
        return {"name": name, "prio": 1, "enabled": True}

    def _plan(self, mod, stable_hist, stable_repodata=None):
        pkgs = [self._pkg("CrabFetch")]
        versions = {"CrabFetch": "0.5.4"}
        pilot = {"CrabFetch": [("succeeded", "0.5.4-1", 11031313)]}
        pilot_done = {"CrabFetch": {"0.5.4"}}          # pilot: 4/4 в repodata
        stable_done = stable_repodata or {}
        return mod.build_plan(
            pkgs, versions, pilot, 10,
            stable_hist={"CrabFetch": stable_hist},
            pilot_published=pilot_done, pilot_completed=pilot_done,
            stable_published=stable_done, stable_completed=stable_done)

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
        """stable 4/4 в repodata => пересборки нет, волна пропускает пакет."""
        done = {"CrabFetch": {"0.5.4"}}
        assert self._plan(mod, [("succeeded", "0.5.4-1", 11045000)],
                          stable_repodata=done) == []

    def test_green_stable_build_without_repodata_is_still_work(self, mod):
        """Зелёная сборка stable без repodata НЕ закрывает цель (P0 #2)."""
        plan = self._plan(mod, [("succeeded", "0.5.4-1", 11045000)])
        assert [p["name"] for p in plan] == ["CrabFetch"]

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
                "pilot_srpm_build": 11031313,
                "stable_build": None, "pilot_ok": True, "stable_ok": False,
                "mode": "promote-only", "force": False}
        base.update(kw)
        return base

    def test_promote_only_reuses_pilot_srpm_skips_pilot(self, mod, tmp_path,
                                                              monkeypatch):
        """pilot-цель зелёная => pilot НЕ пересобирается, stable получает тот же SRPM."""
        calls = {}

        def fake_fetch(name, target, builds, dest_dir, build_id=None):
            calls["fetch"] = (name, target)
            calls["build_id"] = build_id
            p = dest_dir / "CrabFetch-0.5.4-1.fc44.src.rpm"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"srpm-bytes")
            return str(p), "deadbeef", 11031313

        def fake_submit(project, srpm, conf, chroots=None):
            calls["submit"] = project
            return "11042000"

        def fake_wait(bid, chroots, token, timeout_min, interval):
            calls["waited"] = bid
            return 0, {c: "succeeded" for c in chroots}, "4/4 ok"

        monkeypatch.setattr(mod, "fetch_pilot_srpm", fake_fetch)
        monkeypatch.setattr(mod, "submit", fake_submit)
        monkeypatch.setattr(mod, "wait_exact_4_4", fake_wait)
        # вердикт даёт repodata: к моменту проверки stable уже опубликован
        monkeypatch.setattr(mod, "verify_published", lambda *a, **kw: (True, {}))

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
        calls_chroots = {}

        def fake_make(name, target, log_dir):
            p = tmp_path / "SRPMS" / f"{name}-{target}-1.fc44.src.rpm"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")
            return str(p)

        def fake_submit(project, srpm, conf, chroots=None):
            order.append(f"submit:{project}")
            calls_chroots.setdefault(project, list(chroots or []))
            return "111"

        def fake_wait(bid, chroots, token, timeout_min, interval):
            order.append(f"wait:{bid}")
            return 0, {c: "succeeded" for c in chroots}, "4/4 ok"

        monkeypatch.setattr(mod, "make_srpm", fake_make)
        monkeypatch.setattr(mod, "submit", fake_submit)
        monkeypatch.setattr(mod, "wait_exact_4_4", fake_wait)
        # репозиторий пуст => недостающие ВСЕ chroot, публикация после wait
        monkeypatch.setattr(mod, "verify_published", lambda *a, **kw: (True, {}))
        res = mod.process_package(self._pkg(mode="full", pilot_ok=False),
                                  self.CHROOTS, None, None, tmp_path / "logs",
                                  1.0, {}, [])
        assert res["outcome"] == "promoted"
        assert order == [f"submit:{mod.PILOT}", "wait:111",
                         f"submit:{mod.STABLE}", "wait:111"]
        # первый submit идёт на все четыре chroot (ничего ещё не опубликовано)
        assert calls_chroots[mod.PILOT] == self.CHROOTS
        assert res["pilot_missing_chroots"] == self.CHROOTS

    def test_submit_sends_only_missing_chroots_not_all_four(self, mod, tmp_path,
                                                            monkeypatch):
        """P0: при частично опубликованном репо submit уходит ТОЛЬКО на недостающее.

        Раньше submit всегда шёл на все четыре chroot'а. Повторная отправка
        уже опубликованного target не добавляет надёжности, но подрывает
        три зелёных chroot'а: новая сборка перекрывает их ровно тем же SRPM
        и какое-то время держит repodata неполной. Вариант «3/4 из прошлой
        волны + дозалив одного chroot» — ровно тот случай, ради которого
        repodata и стал единственным вердиктом.
        """
        target, name = "3.7.1", "dysk"
        full = {c: "succeeded" for c in self.CHROOTS}
        # три chroot'а уже несут target, четвёртого нет
        done = [c for c in self.CHROOTS if c != "leap-aarch64"]
        pilot_per = {c: {name: {target}} for c in done}
        pilot_per["leap-aarch64"] = {}

        monkeypatch.setattr(mod, "probe_all",
                            probe_stub(mod, pilot_per=pilot_per, stable_per={}))
        submits, waits = [], []
        monkeypatch.setattr(
            mod, "submit",
            lambda project, srpm, conf, chroots=None:
                (submits.append((project, list(chroots or []))), "11065107")[1])
        monkeypatch.setattr(
            mod, "await_stage",
            lambda project, chroots, name, target, bid, stage, token, deadline,
                   cap, interval, wait_chroots, out:
                (waits.append((project, list(wait_chroots))), ("complete", {}))[1])
        monkeypatch.setattr(mod, "verify_published", lambda *a, **kw: (True, {}))

        # 3/4: пересобирать нельзя. make_srpm должен молчать — иначе в
        # недостающий chroot ушёл бы ДРУГОЙ артефакт, чем в три опубликованных.
        forbidden = []
        monkeypatch.setattr(
            mod, "make_srpm",
            lambda *a, **kw: forbidden.append("make_srpm") or "/forbidden")
        exact = fake_pilot_srpm(monkeypatch, mod)
        submitted_srpm = []
        monkeypatch.setattr(
            mod, "submit",
            lambda project, srpm, conf, chroots=None:
                (submits.append((project, list(chroots or []))),
                 submitted_srpm.append(srpm), "11065107")[2])
        monkeypatch.setattr(mod, "sha256_file", lambda p: "aa")

        res = mod.process_package(
            {"name": name, "target": target, "pilot_build": None,
             "pilot_srpm_build": 11064066,
             "stable_build": None, "pilot_ok": False, "stable_ok": False,
             "mode": "full", "force": False},
            self.CHROOTS, None, None, tmp_path / "logs", 1.0, {}, [])

        assert res["pilot_missing_chroots"] == ["leap-aarch64"]
        pilot_submits = [c for proj, c in submits if proj == mod.PILOT]
        assert pilot_submits == [["leap-aarch64"]], (
            f"submit обязан уходить только на недостающий chroot, получено {pilot_submits}")
        assert res.get("pilot_submit_chroots") == ["leap-aarch64"]
        # наблюдение тоже ведётся по недостающему, а не по всем четырём
        assert [c for proj, c in waits if proj == mod.PILOT] == [["leap-aarch64"]]
        assert res["outcome"] == "promoted"

        # P0 exact-SRPM: make_srpm в 3/4 не вызывается вовсе
        assert forbidden == [], "3/4 обязан брать SRPM pilot-сборки, а не пересобирать"
        assert res["srpm_source"] == "pilot-build:11064066"
        assert exact["build_id"] == 11064066, (
            "3/4 обязан идти за SRPM в ТОЧНЫЙ source build из плана")
        # и в pilot-недостающий chroot, и в stable ушёл один и тот же файл
        stable_srpm = [p for proj, p in zip([a[0] for a in submits], submitted_srpm)
                       if proj == mod.STABLE]
        assert stable_srpm == submitted_srpm[:1], "stable должен получить тот же SRPM"
        assert_srpm_bytes(submitted_srpm[0])
        assert_srpm_bytes(stable_srpm[0])

    def test_partially_published_pilot_is_not_green_without_the_missing_one(
            self, mod, tmp_path, monkeypatch):
        """Три из четырёх в repodata — это ещё не цель: отсутствие не считается."""
        target, name = "3.7.1", "dysk"
        done = [c for c in self.CHROOTS if c != "leap-aarch64"]
        per = {c: {name: {target}} for c in done}
        per["leap-aarch64"] = {}
        probe = probe_stub(mod, pilot_per=per, stable_per={})

        per = probe(mod.PILOT, self.CHROOTS)["per_chroot"]
        assert mod.missing_chroots(mod.PILOT, self.CHROOTS, name, target,
                                   per) == ["leap-aarch64"]
        assert mod.is_complete(per, name, target) is False, (
            "усечение проверки до «где-нибудь опубликовано» вернуло бы 3/4 = успех")

        # а репо, где есть все четыре, цель закрывает
        full = {c: {name: {target}} for c in self.CHROOTS}
        assert mod.is_complete(full, name, target) is True

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
        done = {"bandwhich": {"0.23.1"}}
        plan = mod.build_plan(pkgs, versions, {}, 5, stable_hist={},
                              pilot_published=done, stable_published=done,
                              pilot_completed=done, stable_completed=done)
        assert plan == []

    def test_repodata_closes_gap_without_build_list_success(self, mod):
        """Зелёной записи в build-list нет, но версия опубликована — работа не нужна."""
        pkgs = [self._pkg("csview")]
        versions = {"csview": "1.3.4"}
        done = {"csview": {"1.3.4"}}
        plan = mod.build_plan(pkgs, versions, {}, 5, stable_hist={},
                              pilot_published=done, stable_published=done,
                              pilot_completed=done, stable_completed=done)
        assert plan == []

    def test_stable_gap_still_planned_even_if_pilot_published(self, mod):
        pkgs = [self._pkg("pybonsai")]
        versions = {"pybonsai": "1.0.0"}
        plan = mod.build_plan(pkgs, versions, {}, 5, stable_hist={},
                              pilot_published={"pybonsai": {"1.0.0"}},
                              stable_published={},
                              pilot_completed={"pybonsai": {"1.0.0"}},
                              stable_completed={})
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

    def test_without_repodata_nothing_is_considered_done(self, mod):
        """repodata=None => пустые таблицы, план не падает, но и не закрывает цели.

        Раньше здесь проверялось, что зелёная история подгоняет план до одного
        пакета. Это и был P0 #2: неизвестное состояние репозитория не должно
        выглядеть как «уже опубликовано», иначе пустая repodata молча
        засчитала бы готовность всем пакетам сразу.
        """
        pkgs = [self._pkg("fresh"), self._pkg("baked")]
        versions = {"fresh": "2.0.0", "baked": "1.0.0"}
        hist = {"baked": [("succeeded", "1.0.0", 1)]}
        stable = {"baked": [("succeeded", "1.0.0-1", 2)]}
        plan = mod.build_plan(pkgs, versions, hist, 10, stable_hist=stable)
        assert {p["name"] for p in plan} == {"fresh", "baked"}, (
            "без repodata ни одна цель не может считаться достигнутой")


class TestProbeFailClosed:
    def test_probe_error_becomes_plan_error(self, mod, monkeypatch):
        def boom(*a, **kw):
            raise mod.PublishProbeError("404")
        monkeypatch.setattr(mod, "probe_all", mod._real_probe_all)
        monkeypatch.setattr(mod, "fetch_published_per_chroot", boom)
        with pytest.raises(mod.WavePlanError) as exc:
            mod.probe_published(mod.STABLE, ["opensuse-tumbleweed-x86_64"])
        assert "repodata" in str(exc.value)

    def test_probe_error_stops_run_before_any_build(self, mod, monkeypatch, tmp_path,
                                                    capfd):
        def boom(*a, **kw):
            raise mod.PublishProbeError("404")
        monkeypatch.setattr(mod, "probe_all", mod._real_probe_all)
        monkeypatch.setattr(mod, "fetch_published_per_chroot", boom)

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

class TestPartialChrootRemediation:
    """Сценарий 3/4 -> повтор только недостающего chroot -> агрегированные 4/4.

    Реальный кейс 2026-10-02 (dysk/dua/ghfetch/pokemon-icat): COPR собрал 3 из
    4 chroot, четвёртый упал по внешней причине. Ключевые инварианты:
      * одна неуспешная сборка НЕ считается 4/4 и не двигает пакет в stable;
      * версия, опубликованная лишь в 3 из 4 repodata, НЕ закрывает цель
        (union-таблица для этого не годится);
      * повтор не пересобирает уже зелёные chroot и не дублирует SRPM;
      * когда все четыре chroot опубликованы, цель закрывается и пакет идёт
        в stable в режиме promote-only с тем же SRPM.
    """

    CHROOTS = ["tw-x86_64", "tw-aarch64", "leap-x86_64", "leap-aarch64"]

    def _pkg(self, name="dysk", **kw):
        base = {"name": name, "prio": 5, "enabled": True}
        base.update(kw)
        return base

    def test_three_of_four_chroots_is_not_four_of_four(self, mod):
        """3/4 в repodata => цель НЕ достигнута, пакет остаётся в плане."""
        union = {"dysk": {"3.7.1"}}                    # есть хоть где-то
        completed = {}                                  # нет нигде во всех сразу
        plan = mod.build_plan([self._pkg()], {"dysk": "3.7.1"}, {}, 5, stable_hist={},
                              pilot_published=union, stable_published={},
                              pilot_completed=completed, stable_completed={})
        assert [p["name"] for p in plan] == ["dysk"]

    def test_union_table_alone_never_counts_as_done(self, mod):
        """Регрессия P0: union-таблица не должна закрывать цель."""
        union = {"dysk": {"3.7.1"}}
        plan = mod.build_plan([self._pkg()], {"dysk": "3.7.1"}, {}, 5, stable_hist={},
                              pilot_published=union, stable_published=union,
                              pilot_completed={}, stable_completed={})
        assert plan, "union-репо не должен засчитываться как 4/4"

    def test_aggregated_four_of_four_closes_gap(self, mod):
        """Как только версия опубликована во всех 4 chroot — цель закрыта."""
        done = {"dysk": {"3.7.1"}}
        plan = mod.build_plan([self._pkg()], {"dysk": "3.7.1"}, {}, 5, stable_hist={},
                              pilot_published=done, stable_published={},
                              pilot_completed=done, stable_completed={})
        assert [p["name"] for p in plan] == ["dysk"]        # stable ещё пуст
        assert plan[0]["mode"] == "promote-only"             # pilot-зелёный => без пересборки
        assert plan[0]["pilot_build"] is None                # pilot не пересобираем

    def test_completed_table_is_intersection_not_union(self, mod):
        """Ключи — chroot'ы: без них не видно, какой именно не дособран."""
        per_chroot = {
            "tw-x86_64":   {"dysk": {"3.7.1"}, "gum": {"2.0.2"}},
            "tw-aarch64":  {"dysk": {"3.7.1"}, "gum": {"2.0.2"}},
            "leap-x86_64": {"dysk": {"3.7.1"}, "gum": {"2.0.2"}},
            "leap-aarch64": {"dysk": {"3.7.0"}, "gum": {"2.0.2"}},  # dysk СТАРАЯ
        }
        done = mod.completed_table(per_chroot)
        assert "dysk" not in done, "3.7.1 не во всех chroot => не completed"
        assert done["gum"] == {"2.0.2"}               # 2.0.2 есть во всех четырёх
        assert mod.completed_table({}) == {}
        # union-таблица для сравнения: там 3.7.1 «есть» — и она не должна решать
        union: dict = {}
        for table in per_chroot.values():
            for name, versions in table.items():
                union.setdefault(name, set()).update(versions)
        assert "3.7.1" in union["dysk"]
        assert "dysk" not in done
        # а по какому именно chroot не добрали — видно из per-chroot
        assert mod.chroot_presence(per_chroot, "dysk", "3.7.1") == {
            "tw-x86_64", "tw-aarch64", "leap-x86_64"}
        assert mod.is_complete(per_chroot, "dysk", "3.7.1") is False
        assert mod.is_complete(per_chroot, "gum", "2.0.2") is True

    def test_completed_table_drops_package_absent_from_any_chroot(self, mod):
        per_chroot = {"a": {"gum": {"2.0.2"}}, "b": {"gum": {"2.0.2"}},
                      "c": {}, "d": {"gum": {"2.0.2"}}}
        assert mod.completed_table(per_chroot) == {}

    def test_failed_chroot_gives_pilot_not_clean_and_no_stable(self, mod, tmp_path,
                                                               monkeypatch):
        """3/4 терминально => pilot-not-clean, stable не submit'ится."""
        submits = []
        seen = fake_pilot_srpm(monkeypatch, mod)
        monkeypatch.setattr(mod, "sha256_file", lambda p: "deadbeef")
        monkeypatch.setattr(mod, "submit",
                            lambda p, s, c, chroots=None:
                                (submits.append((p, list(chroots or []))), "11065000")[1])
        # repodata: целевая версия есть в 3 из 4 chroot (leap-aarch64 остался старым)
        monkeypatch.setattr(mod, "probe_all", probe_stub(
            mod, pilot_per={"tw-x86_64": {"dysk": {"3.7.1"}},
                            "tw-aarch64": {"dysk": {"3.7.1"}},
                            "leap-x86_64": {"dysk": {"3.7.1"}},
                            "leap-aarch64": {"dysk": {"3.7.0"}}}))
        seen = {"tw-x86_64": "succeeded", "tw-aarch64": "succeeded",
                "leap-x86_64": "succeeded", "leap-aarch64": "failed"}
        monkeypatch.setattr(mod, "wait_exact_4_4",
                            lambda *a, **kw: (1, seen, "3/4"))
        monkeypatch.setattr(mod, "build_still_active", lambda *a, **kw: False)
        pkg = {"name": "dysk", "target": "3.7.1", "pilot_build": None,
               "pilot_srpm_build": 11064066,
               "stable_build": None, "pilot_ok": False, "stable_ok": False,
               "mode": "full", "force": False}
        res = mod.process_package(pkg, self.CHROOTS, None, None, tmp_path / "logs",
                                  1.0, {}, [])
        assert res["outcome"] == "pilot-not-clean"
        assert submits == [(mod.PILOT, ["leap-aarch64"])], (
            "пересобирать можно ТОЛЬКО неопубликованный chroot")
        assert res["srpm_source"].startswith("pilot-build:"), (
            "3/4 обязан брать SRPM существующей pilot-сборки, а не пересобирать")
        assert res["pilot_chroots"]["leap-aarch64"] == "failed"
        assert res.get("stable_submitted") is None, "pilot 3/4 не продвигается в stable"

    def test_single_chroot_build_is_not_success(self, mod, tmp_path, monkeypatch):
        """Одиночная сборка одного chroot (как наш canary) != 4/4."""
        seen = {"tw-aarch64": "succeeded", "tw-x86_64": "missing",
                "leap-x86_64": "missing", "leap-aarch64": "missing"}
        seen = fake_pilot_srpm(monkeypatch, mod)
        monkeypatch.setattr(mod, "sha256_file", lambda p: "deadbeef")
        monkeypatch.setattr(mod, "submit", lambda p, s, c, chroots=None: "11065107")
        monkeypatch.setattr(mod, "wait_exact_4_4", lambda *a, **kw: (1, seen, "1/4"))
        monkeypatch.setattr(mod, "build_still_active", lambda *a, **kw: False)
        pkg = {"name": "dysk", "target": "3.7.1", "pilot_build": 11064066,
               "pilot_srpm_build": 11064066,
               "stable_build": None, "pilot_ok": False, "stable_ok": False,
               "mode": "full", "force": False}
        res = mod.process_package(pkg, self.CHROOTS, None, None, tmp_path / "logs",
                                  1.0, {}, [])
        assert res["outcome"] != "promoted"
        assert res["outcome"] == "pilot-not-clean"
        assert res.get("pilot_resumed") is True      # наблюдение, не resubmit
        assert seen["build_id"] == 11064066, (
            "resumed-путь обязан брать SRPM ТОЙ сборки, что в плане")

    def test_missing_chroot_retry_then_promote_only_same_srpm(self, mod, tmp_path,
                                                               monkeypatch):
        """Повтор, давший 4/4, ведёт в stable в promote-only с ТЕМ ЖЕ SRPM."""
        calls = {}

        def fake_fetch(name, target, builds, dest_dir, build_id=None):
            calls["fetch"] = (name, target)
            p = dest_dir / "dysk-3.7.1-1.fc44.src.rpm"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"srpm-bytes")
            return str(p), "657699b7", 11064066

        monkeypatch.setattr(mod, "fetch_pilot_srpm", fake_fetch)
        monkeypatch.setattr(mod, "submit",
                            lambda p, s, c, chroots=None:
                                calls.setdefault("submit", (p, list(chroots or [])))
                                and None or "11065107")
        monkeypatch.setattr(mod, "wait_exact_4_4",
                            lambda *a, **kw: (0, {c: "succeeded" for c in self.CHROOTS}, "4/4"))
        # repodata: публикация стала полной именно после этой сборки
        monkeypatch.setattr(mod, "verify_published", lambda *a, **kw: (True, {}))
        pkg = {"name": "dysk", "target": "3.7.1", "pilot_build": None,
               "pilot_srpm_build": 11064066,
               "stable_build": None, "pilot_ok": True, "stable_ok": False,
               "mode": "promote-only", "force": False}
        res = mod.process_package(pkg, self.CHROOTS, None, None, tmp_path / "logs",
                                  1.0, {}, [])
        assert res["outcome"] == "promoted"
        assert calls["fetch"] == ("dysk", "3.7.1")
        assert res["srpm_source"] == "pilot-build:11064066"   # тот же артефакт
        assert calls["submit"][0] == mod.STABLE               # pilot не пересобирался
        assert calls["submit"][1] == self.CHROOTS             # репо пуст => все четыре

    def test_deferred_when_copr_slower_than_job(self, mod, tmp_path, monkeypatch):
        """Таймаут наблюдения при живой сборке => deferred, а не провал и не успех."""
        monkeypatch.setattr(mod, "make_srpm", lambda *a, **kw: "srpm")
        monkeypatch.setattr(mod, "sha256_file", lambda p: "deadbeef")
        monkeypatch.setattr(mod, "submit",
                            lambda p, s, c, chroots=None: "11066000")
        monkeypatch.setattr(mod, "wait_exact_4_4", lambda *a, **kw: (1, {}, "timeout"))
        monkeypatch.setattr(mod, "build_still_active", lambda *a, **kw: True)
        pkg = {"name": "dysk", "target": "3.7.1", "pilot_build": None,
               "stable_build": None, "pilot_ok": False, "stable_ok": False,
               "mode": "full", "force": False}
        res = mod.process_package(pkg, self.CHROOTS, None, None, tmp_path / "logs",
                                  1.0, {}, [])
        assert res["outcome"] == "deferred"
        assert res["deferred_stage"] == "pilot"
        assert str(res["pilot_build"]) == "11066000"
        assert res["outcome"] not in mod.SUCCESS_OUTCOMES

    def test_active_build_is_resumed_not_resubmitted(self, mod):
        """Следующая волна берёт существующий build id, а не submit'ит дубль."""
        hist = {"dysk": [("running", "3.7.1-1.fc44", 11066000)]}
        assert mod.active_build(hist["dysk"], "3.7.1") == 11066000
        plan = mod.build_plan([self._pkg()], {"dysk": "3.7.1"},
                              {"dysk": [("running", "3.7.1-1.fc44", 11066000)]},
                              5, stable_hist={}, pilot_published={}, stable_published={},
                              pilot_completed={}, stable_completed={})
        assert plan[0]["pilot_build"] == 11066000     # resume, не новый submit

    def test_deferred_is_reported_as_failure_of_run_but_not_hard(self, mod):
        items = [{"name": "dysk", "outcome": "deferred"},
                 {"name": "gum", "outcome": "promoted"}]
        assert [i["name"] for i in mod.deferred_items(items)] == ["dysk"]
        assert [i["name"] for i in mod.failed_items(items)] == ["dysk"]


class TestExactSrpmIdentity:
    """P0: remediation 3/4 и resume обязаны работать с ТЕМ ЖЕ SRPM, что в pilot.

    Свежий make_srpm() в этих сценариях дал бы другой артефакт, и в репозитории
    оказались бы два SRPM на один target: три chroot'а — от старого, четвёртый и
    stable — от нового. Ниже фиксируется и запрет make_srpm, и то, что файл,
    который реально уходит в submit, байт-в-байт совпадает со скачанным.
    """

    URL = "https://copr.example/pilot/3.7.1/dysk-3.7.1-1.fc44.src.rpm"
    PAYLOAD = b"\x1f\x8bEXACT-PILOT-SRPM-BYTES\x00\x01\x02" * 7

    @staticmethod
    def _build(bid, state, url=URL, version="3.7.1", name="dysk"):
        return {"id": bid, "state": state,
                "source_package": {"name": name, "version": version, "url": url}}

    @staticmethod
    def _install_urlopen(monkeypatch, mod, served):
        """served: url -> bytes. Возвращает список реально запрошенных url."""
        import urllib.request as _u

        class _Resp:
            def __init__(self, payload):
                self._p, self._done = payload, False

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, size=-1):
                if self._done:
                    return b""
                self._done = True
                return self._p

        asked = []

        def fake_urlopen(url, timeout=None, data=None):
            asked.append(str(url))
            if str(url) not in served:
                raise OSError(f"не отдал сервер: {url}")
            return _Resp(served[str(url)])

        monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
        return asked

    def test_fetch_is_byte_identical_and_sha_matches(self, mod, tmp_path, monkeypatch):
        asked = self._install_urlopen(monkeypatch, mod, {self.URL: self.PAYLOAD})
        path, sha, bid = mod.fetch_pilot_srpm("dysk", "3.7.1",
                                              [self._build(11064066, "succeeded")],
                                              tmp_path / "SRPMS",
                                              build_id=11064066)
        assert asked == [self.URL]
        with open(path, "rb") as fh:
            assert fh.read() == self.PAYLOAD, "сохранённый SRPM обязан быть байт-в-байт"
        assert sha == hashlib.sha256(self.PAYLOAD).hexdigest()
        assert sha == mod.sha256_file(path), "возвращённый sha256 обязан совпадать с файлом"
        assert bid == 11064066

    def test_plan_build_id_wins_over_succeeded(self, mod, tmp_path, monkeypatch):
        """Resumed: берём SRPM ровно той сборки, что в плане, а не succeeded."""
        active_url = "https://copr.example/active/3.7.1/dysk-3.7.1-1.fc44.src.rpm"
        builds = [self._build(11064066, "succeeded"),
                  self._build(11065107, "running", url=active_url)]
        asked = self._install_urlopen(monkeypatch, mod,
                                      {active_url: self.PAYLOAD, self.URL: b"OTHER-SRPM"})
        path, sha, bid = mod.fetch_pilot_srpm(
            "dysk", "3.7.1", builds, tmp_path / "SRPMS", build_id=11065107)
        assert asked == [active_url], "нельзя молча подменять на succeeded-сборку"
        assert bid == 11065107
        assert sha == hashlib.sha256(self.PAYLOAD).hexdigest()

    def test_plan_build_missing_is_hard_error(self, mod, tmp_path, monkeypatch):
        self._install_urlopen(monkeypatch, mod, {self.URL: self.PAYLOAD})
        with pytest.raises(mod.WaveError) as ei:
            mod.fetch_pilot_srpm("dysk", "3.7.1",
                                 [self._build(11064066, "succeeded")],
                                 tmp_path / "SRPMS", build_id=99999999)
        assert "99999999" in str(ei.value)

    def test_partial_without_srpm_is_hard_error(self, mod, tmp_path, monkeypatch):
        """3/4 без скачиваемого SRPM = не пытаемся пересобрать, а падаем."""
        self._install_urlopen(monkeypatch, mod, {})
        with pytest.raises(mod.WaveError) as ei:
            mod.fetch_pilot_srpm("dysk", "3.7.1", [], tmp_path / "SRPMS")
        assert "byte-идентичности" in str(ei.value)

    @pytest.mark.parametrize("scenario", ["partial-3-of-4", "resumed-pilot"])
    def test_make_srpm_is_forbidden_in_partial_and_resume(self, mod, scenario, tmp_path,
                                                          monkeypatch):
        forbidden = []
        monkeypatch.setattr(mod, "make_srpm",
                            lambda *a, **kw: forbidden.append(a) or "/forbidden")
        monkeypatch.setattr(mod, "sha256_file", lambda p: "aa")
        seen = fake_pilot_srpm(monkeypatch, mod)
        monkeypatch.setattr(mod, "probe_all", probe_stub(
            mod,
            pilot_per={"tw-x86_64": {"dysk": {"3.7.1"}}, "tw-aarch64": {"dysk": {"3.7.1"}},
                       "leap-x86_64": {"dysk": {"3.7.1"}}, "leap-aarch64": {}},
            stable_per={}))
        monkeypatch.setattr(
            mod, "submit", lambda p, s, c, chroots=None: "11065107")
        monkeypatch.setattr(
            mod, "await_stage",
            lambda *a, **kw: ("complete", {c: {"dysk": {"3.7.1"}} for c in self_chroots()}))
        monkeypatch.setattr(mod, "verify_published", lambda *a, **kw: (True, {}))

        pkg = {"name": "dysk", "target": "3.7.1",
               "pilot_build": 11064066 if scenario == "resumed-pilot" else None,
               "pilot_srpm_build": 11064066,
               "stable_build": None, "pilot_ok": False, "stable_ok": False,
               "mode": "full", "force": False}
        res = mod.process_package(pkg, ["tw-x86_64", "tw-aarch64",
                                        "leap-x86_64", "leap-aarch64"],
                                  None, None, tmp_path / "logs", 1.0, {}, [])

        assert forbidden == [], f"{scenario}: make_srpm() вызван, exact-SRPM нарушен"
        assert res["srpm_source"] == "pilot-build:11064066"
        if scenario == "resumed-pilot":
            assert seen["build_id"] == 11064066, "resume обязан идти за SRPM в плановую сборку"

    def test_make_srpm_still_used_when_pilot_never_saw_target(self, mod, tmp_path,
                                                              monkeypatch):
        """Свежая полная сборка (репо пусто) — make_srpm обязателен, это не запрещено."""
        calls = []
        monkeypatch.setattr(mod, "make_srpm",
                            lambda n, t, d: calls.append(n) or str(tmp_path / "fresh.src.rpm"))
        monkeypatch.setattr(mod, "sha256_file", lambda p: "aa")
        monkeypatch.setattr(mod, "probe_all", probe_stub(
            mod, pilot_per={c: {} for c in self_chroots()},
            stable_per={c: {} for c in self_chroots()}))
        monkeypatch.setattr(mod, "submit", lambda p, s, c, chroots=None: "11065107")
        monkeypatch.setattr(mod, "await_stage",
                            lambda *a, **kw: ("complete", {}))
        monkeypatch.setattr(mod, "verify_published", lambda *a, **kw: (True, {}))

        res = mod.process_package({"name": "dysk", "target": "3.7.1", "pilot_build": None,
                                   "stable_build": None, "pilot_ok": False,
                                   "stable_ok": False, "mode": "full", "force": False},
                                  self_chroots(), None, None, tmp_path / "logs",
                                  1.0, {}, [])
        assert calls == ["dysk"]
        assert res["srpm_source"] == "fresh-make-srpm"


class TestProvenanceSeparation:
    """P0: плановый build и provenance SRPM — РАЗНЫЕ поля.

    Регрессии, которые здесь закреплены:
      * у терминального 3/4 `pilot_build` = None, но provenance-источник
        обязан присутствовать как отдельный `pilot_srpm_build`;
      * fetch идёт строго по ТОЧНОМУ id; никакого «найди succeeded-сборку
        той же версии» — подмена дала бы второй SRPM на один target;
      * helper `succeeded_build_url` удалён как таковой.
    """

    TERMINAL = {"dua": 11063545, "dysk": 11064066,
                "ghfetch": 11064212, "pokemon-icat": 11064380}

    @staticmethod
    def _build(bid, state, name="dysk", version="3.7.1",
               url="https://copr.example/dysk-3.7.1-1.fc44.src.rpm"):
        return {"id": bid, "state": state,
                "source_package": {"name": name, "version": version, "url": url}}

    def test_terminal_ids_are_pinned_exactly(self, mod):
        assert mod.PILOT_SRPM_PROVENANCE == self.TERMINAL

    def test_legacy_succeeded_helper_is_gone(self, mod):
        assert not hasattr(mod, "succeeded_build_url"), (
            "поиск succeeded-сборки той же версии обязан быть удалён")

    def test_terminal_failed_parent_keeps_resume_and_provenance_apart(self, mod):
        """3/4: resume-слот пуст, provenance-слот указывает на source build."""
        hist = {"dysk": [("failed", "3.7.1-1.fc44", 11064066)]}
        plan = mod.build_plan([{"name": "dysk", "prio": 5, "enabled": True}],
                              {"dysk": "3.7.1"}, hist, 5, stable_hist={},
                              pilot_published={}, stable_published={},
                              pilot_completed={}, stable_completed={})
        assert len(plan) == 1
        entry = plan[0]
        assert entry["pilot_build"] is None, (
            "терминальная сборка не активна — resume-слот должен быть пуст")
        assert entry["pilot_srpm_build"] == 11064066, (
            "provenance обязан указывать на источник опубликованных chroot'ов")

    @pytest.mark.parametrize("name", sorted(TERMINAL))
    def test_provenance_survives_into_the_plan_entry(self, mod, name):
        hist = {name: [("failed", "3.7.1-1.fc44", 42)]}
        plan = mod.build_plan([{"name": name, "prio": 5, "enabled": True}],
                              {name: "3.7.1"}, hist, 5, stable_hist={},
                              pilot_published={}, stable_published={},
                              pilot_completed={}, stable_completed={})
        assert plan[0]["pilot_srpm_build"] == self.TERMINAL[name]
        assert plan[0]["pilot_build"] is None

    def test_fetch_without_build_id_is_fail_closed(self, mod, tmp_path):
        """Нет id в плане — НЕ пробуем succeeded-fallback, а отказываем."""
        builds = [self._build(11064066, "succeeded")]
        with pytest.raises(mod.WaveError, match="pilot_srpm_build"):
            mod.fetch_pilot_srpm("dysk", "3.7.1", builds,
                                 tmp_path / "SRPMS", build_id=None)

    def test_fetch_with_unknown_id_does_not_search_for_anything(self, mod, tmp_path):
        builds = [self._build(11064066, "succeeded"),
                  self._build(11066000, "failed")]
        with pytest.raises(mod.WaveError, match="не найдена"):
            mod.fetch_pilot_srpm("dysk", "3.7.1", builds,
                                 tmp_path / "SRPMS", build_id=11099999)

    def test_fetch_rejects_foreign_package(self, mod, tmp_path):
        builds = [self._build(11064066, "succeeded", name="dua")]
        with pytest.raises(mod.WaveError, match="принадлежит dua"):
            mod.fetch_pilot_srpm("dysk", "3.7.1", builds,
                                 tmp_path / "SRPMS", build_id=11064066)

    def test_fetch_rejects_version_mismatch(self, mod, tmp_path):
        builds = [self._build(11064066, "succeeded", version="3.7.2")]
        with pytest.raises(mod.WaveError, match="не совпадает"):
            mod.fetch_pilot_srpm("dysk", "3.7.1", builds,
                                 tmp_path / "SRPMS", build_id=11064066)

    def test_fetch_rejects_build_without_srpm_url(self, mod, tmp_path):
        builds = [self._build(11064066, "succeeded", url="")]
        with pytest.raises(mod.WaveError, match="без SRPM URL"):
            mod.fetch_pilot_srpm("dysk", "3.7.1", builds,
                                 tmp_path / "SRPMS", build_id=11064066)
