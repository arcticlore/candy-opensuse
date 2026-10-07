#!/usr/bin/env python3
"""test_wave_gates.py — сверка отчётов с планом и точный sync-diff.

Два гейта, закрывших P0:

  * wave_report_gate: отчёты пакетов обязаны совпадать с plan.json по name и
    target — дубли, лишние, пропущенные и target mismatch запрещены ДО того,
    как что-то попадёт в wave-report и state.
  * wave_sync_gate: синхронизация обязана трогать ровно state/state.json
    (только promoted-ключи с exact target) и SPECS/<promoted>.spec. Любой
    другой путь роняет reconcile. Валидация идёт в рабочем дереве ДО коммита:
    после git commit git status чист, и гейт проверял бы пустоту.
"""
import json
import os
import subprocess

import pytest

from conftest import ROOT, load_module

report_gate = load_module("wave_report_gate",
                          os.path.join(ROOT, "bin/wave_report_gate.py"))
sync_gate = load_module("wave_sync_gate",
                        os.path.join(ROOT, "bin/wave_sync_gate.py"))


def _write_report(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": items}, ensure_ascii=False))


def _plan(path, packages):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"packages": packages, "plan_size": len(packages)}))


class TestReportGateMatchesPlan:
    def test_matching_reports_pass(self, tmp_path):
        _plan(tmp_path / "plan.json",
              [{"name": "dysk", "target": "3.7.1"},
               {"name": "dua", "target": "2.32.0"}])
        _write_report(tmp_path / "logs/report-dysk.json",
                      [{"name": "dysk", "target": "3.7.1", "outcome": "promoted"}])
        _write_report(tmp_path / "logs/report-dua.json",
                      [{"name": "dua", "target": "2.32.0", "outcome": "deferred"}])
        assert report_gate.reconcile(str(tmp_path / "plan.json"),
                                     str(tmp_path / "logs/report-*.json")) == []

    def test_missing_report_is_rejected(self, tmp_path):
        _plan(tmp_path / "plan.json",
              [{"name": "dysk", "target": "3.7.1"}, {"name": "dua", "target": "2.32.0"}])
        _write_report(tmp_path / "logs/report-dysk.json",
                      [{"name": "dysk", "target": "3.7.1", "outcome": "promoted"}])
        problems = report_gate.reconcile(str(tmp_path / "plan.json"),
                                         str(tmp_path / "logs/report-*.json"))
        assert any("пропущенный пакет: dua" in p for p in problems), problems

    def test_extra_report_is_rejected(self, tmp_path):
        """Пакет, которого не было в плане, не может внезапно «всплыть»."""
        _plan(tmp_path / "plan.json", [{"name": "dysk", "target": "3.7.1"}])
        _write_report(tmp_path / "logs/report-dysk.json",
                      [{"name": "dysk", "target": "3.7.1", "outcome": "promoted"}])
        _write_report(tmp_path / "logs/report-rogue.json",
                      [{"name": "rogue", "target": "9.9.9", "outcome": "promoted"}])
        problems = report_gate.reconcile(str(tmp_path / "plan.json"),
                                         str(tmp_path / "logs/report-*.json"))
        assert any("лишний пакет в отчётах: rogue" in p for p in problems), problems

    def test_target_mismatch_is_rejected(self, tmp_path):
        """Отчёт с самоплёвым target не должен пройти под видом планового."""
        _plan(tmp_path / "plan.json", [{"name": "dysk", "target": "3.7.1"}])
        _write_report(tmp_path / "logs/report-dysk.json",
                      [{"name": "dysk", "target": "3.7.0", "outcome": "promoted"}])
        problems = report_gate.reconcile(str(tmp_path / "plan.json"),
                                         str(tmp_path / "logs/report-*.json"))
        assert any("target mismatch у dysk" in p for p in problems), problems

    def test_duplicate_name_across_reports_is_rejected(self, tmp_path):
        _plan(tmp_path / "plan.json", [{"name": "dysk", "target": "3.7.1"}])
        _write_report(tmp_path / "logs/report-a.json",
                      [{"name": "dysk", "target": "3.7.1", "outcome": "promoted"}])
        _write_report(tmp_path / "logs/report-b.json",
                      [{"name": "dysk", "target": "3.7.1", "outcome": "deferred"}])
        problems = report_gate.reconcile(str(tmp_path / "plan.json"),
                                         str(tmp_path / "logs/report-*.json"))
        assert any("больше одного раза" in p for p in problems), problems

    def test_duplicate_inside_one_report_is_rejected(self, tmp_path):
        _plan(tmp_path / "plan.json", [{"name": "dysk", "target": "3.7.1"}])
        _write_report(tmp_path / "logs/report-dysk.json",
                      [{"name": "dysk", "target": "3.7.1", "outcome": "promoted"},
                       {"name": "dysk", "target": "3.7.1", "outcome": "deferred"}])
        problems = report_gate.reconcile(str(tmp_path / "plan.json"),
                                         str(tmp_path / "logs/report-*.json"))
        assert any("больше одного раза" in p for p in problems), problems

    def test_duplicate_name_in_plan_is_rejected(self, tmp_path):
        _plan(tmp_path / "plan.json",
              [{"name": "dysk", "target": "3.7.1"}, {"name": "dysk", "target": "3.7.2"}])
        problems = report_gate.reconcile(str(tmp_path / "plan.json"),
                                         str(tmp_path / "logs/report-*.json"))
        assert any("план: пакет dysk" in p for p in problems), problems

    def test_reports_without_plan_artifact_fail(self, tmp_path):
        """Скачали отчёты, плана нет — сверять не с чем, итог недостоверен."""
        _write_report(tmp_path / "logs/report-dysk.json",
                      [{"name": "dysk", "target": "3.7.1", "outcome": "promoted"}])
        problems = report_gate.reconcile(str(tmp_path / "plan.json"),
                                         str(tmp_path / "logs/report-*.json"))
        assert problems and "не найден" in problems[0], problems

    def test_no_plan_and_no_reports_is_not_a_failure(self, tmp_path):
        """Упавший plan + пропущенные package-job'ы: сверять нечего, но и врать не о чем."""
        assert report_gate.reconcile(str(tmp_path / "plan.json"),
                                     str(tmp_path / "logs/report-*.json")) == []

    def test_empty_plan_with_stray_report_fails(self, tmp_path):
        _plan(tmp_path / "plan.json", [])
        _write_report(tmp_path / "logs/report-dysk.json",
                      [{"name": "dysk", "target": "3.7.1", "outcome": "promoted"}])
        problems = report_gate.reconcile(str(tmp_path / "plan.json"),
                                         str(tmp_path / "logs/report-*.json"))
        assert any("лишний пакет" in p for p in problems), problems


def _git(repo, *args, env=None):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    if env:
        e.update(env)
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True, env=e).stdout


@pytest.fixture
def repo(tmp_path):
    """Репозиторий с baseline: state.json и двумя спеками."""
    r = tmp_path / "repo"
    (r / "state").mkdir(parents=True)
    (r / "SPECS").mkdir()
    (r / "state/state.json").write_text(json.dumps(
        {"dysk": {"ver": "3.7.0", "ts": 1}, "dua": {"ver": "2.31.0", "ts": 1},
         "gum": {"ver": "2.0.1", "ts": 1}}))
    (r / "SPECS/dysk.spec").write_text("Name: dysk\nVersion: 3.7.0\n")
    (r / "SPECS/dua.spec").write_text("Name: dua\nVersion: 2.31.0\n")
    (r / "SPECS/gum.spec").write_text("Name: gum\nVersion: 2.0.1\n")
    (r / "README.md").write_text("hi\n")
    _git(r, "init", "-q")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "baseline")
    return r


class TestSyncGate:
    def _promoted(self):
        return "dysk@3.7.1"

    def test_only_promoted_changes_pass(self, repo):
        state = json.loads((repo / "state/state.json").read_text())
        state["dysk"]["ver"] = "3.7.1"
        (repo / "state/state.json").write_text(json.dumps(state))
        (repo / "SPECS/dysk.spec").write_text("Name: dysk\nVersion: 3.7.1\n")
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            assert sync_gate.violations("HEAD", sync_gate.parse_promoted(
                self._promoted())) == []
        finally:
            os.chdir(cwd)

    def test_foreign_spec_change_is_a_violation(self, repo):
        state = json.loads((repo / "state/state.json").read_text())
        state["dysk"]["ver"] = "3.7.1"
        (repo / "state/state.json").write_text(json.dumps(state))
        (repo / "SPECS/dysk.spec").write_text("Name: dysk\nVersion: 3.7.1\n")
        (repo / "SPECS/gum.spec").write_text("Name: gum\nVersion: 2.0.2\n")
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            problems = sync_gate.violations("HEAD",
                                            sync_gate.parse_promoted(self._promoted()))
        finally:
            os.chdir(cwd)
        assert any("посторонний путь" in p and "gum.spec" in p for p in problems), problems

    def test_unrelated_path_is_a_violation(self, repo):
        """pkgs.json менять в sync нельзя — это не promoted-target."""
        state = json.loads((repo / "state/state.json").read_text())
        state["dysk"]["ver"] = "3.7.1"
        (repo / "state/state.json").write_text(json.dumps(state))
        (repo / "pkgs.json").write_text("{}")
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            problems = sync_gate.violations("HEAD",
                                            sync_gate.parse_promoted(self._promoted()))
        finally:
            os.chdir(cwd)
        assert any("pkgs.json" in p for p in problems), problems

    def test_non_promoted_state_key_change_is_a_violation(self, repo):
        state = json.loads((repo / "state/state.json").read_text())
        state["dysk"]["ver"] = "3.7.1"
        state["gum"]["ver"] = "2.0.2"          # gum НЕ в promoted
        (repo / "state/state.json").write_text(json.dumps(state))
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            problems = sync_gate.violations("HEAD",
                                            sync_gate.parse_promoted(self._promoted()))
        finally:
            os.chdir(cwd)
        assert any("gum" in p and "не promoted" in p for p in problems), problems

    def test_promoted_ver_must_equal_exact_target(self, repo):
        """state обязан нести ровно target волны, а не другую версию."""
        state = json.loads((repo / "state/state.json").read_text())
        state["dysk"]["ver"] = "3.7.2"          # не 3.7.1
        (repo / "state/state.json").write_text(json.dumps(state))
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            problems = sync_gate.violations("HEAD",
                                            sync_gate.parse_promoted(self._promoted()))
        finally:
            os.chdir(cwd)
        assert any("exact target" in p for p in problems), problems

    def test_job_scratch_dirs_do_not_trip_the_gate(self, repo):
        """logs/, collected/, plan-artifact/ создаёт сам job — это не diff.

        Без фильтра каждый reconcile падал бы на «посторонний путь:
        collected/», и волна не смогла бы закоммитить state/SPECS.
        """
        state = json.loads((repo / "state/state.json").read_text())
        state["dysk"]["ver"] = "3.7.1"
        (repo / "state/state.json").write_text(json.dumps(state))
        (repo / "SPECS/dysk.spec").write_text("Name: dysk\nVersion: 3.7.1\n")
        for d, name in (("logs", "wave-report.json"),
                        ("collected", "report-dysk.json"),
                        ("plan-artifact", "plan.json")):
            (repo / d).mkdir()
            (repo / d / name).write_text("{}")
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            assert sync_gate.violations("HEAD",
                                        sync_gate.parse_promoted(self._promoted())) == []
        finally:
            os.chdir(cwd)

    def test_untracked_foreign_file_still_fails(self, repo):
        """Фильтр scratch не должен прятать настоящие посторонние файлы."""
        state = json.loads((repo / "state/state.json").read_text())
        state["dysk"]["ver"] = "3.7.1"
        (repo / "state/state.json").write_text(json.dumps(state))
        (repo / "bin").mkdir()
        (repo / "bin/evil.py").write_text("import os\n")
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            problems = sync_gate.violations("HEAD",
                                            sync_gate.parse_promoted(self._promoted()))
        finally:
            os.chdir(cwd)
        assert any("bin/evil.py" in p for p in problems), problems

    def test_empty_promoted_list_is_an_error(self, repo):
        assert sync_gate.violations("HEAD", {}) != []

    def test_main_exit_codes(self, repo, capsys):
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            assert sync_gate.main(["--promoted", "dysk@3.7.1", "--base",
                                   "HEAD"]) == 1   # ничего не меняли -> нет promoted-ver
            (repo / "SPECS/rogue.spec").write_text("x\n")
            assert sync_gate.main(["--promoted", "dysk@3.7.1",
                                   "--base", "HEAD"]) == 1
        finally:
            os.chdir(cwd)
        out = capsys.readouterr().err
        assert "WAVE-SYNC-GATE: FAIL" in out


@pytest.fixture(scope="module")
def wf():
    import yaml
    with open(os.path.join(ROOT, ".github/workflows/update.yml"),
              encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class TestGateRunsBeforeCommitInWorkflow:
    """Структурные проверки update.yml: гейт обязан быть между генерацией и коммитом."""

    @staticmethod
    def _steps(wf):
        return [(i, s) for i, s in enumerate(wf["jobs"]["reconcile"]["steps"])]

    def test_order_generate_then_gate_then_commit(self, wf):
        ids = [s.get("id") for _, s in self._steps(wf)]
        assert "generate" in ids and "regen-check" in ids and "sync" in ids
        assert ids.index("generate") < ids.index("regen-check") < ids.index("sync"), (
            "гейт должен проверять рабочее дерево ДО коммита")

    def test_gate_validates_worktree_not_committed_state(self, wf):
        gate = next(s for _, s in self._steps(wf) if s.get("id") == "regen-check")
        run = gate["run"]
        assert "wave_sync_gate.py" in run, "строгий whitelist обязан вызываться"
        assert "regen_gate.py" in run, "spec-set/идемпотентность обязаны проверяться"
        assert "git commit" not in run, "шаг гейта не должен коммитить"
        assert "sync_gate=" in run and "regen_gate=" in run
        # порядок: regen_gate (может перегенерировать) -> wave_sync_gate
        assert run.index("regen_gate.py") < run.index("wave_sync_gate.py"), (
            "wave_sync_gate обязан видеть финальное дерево")

    def test_commit_step_only_when_gate_ok(self, wf):
        commit = next(s for _, s in self._steps(wf) if s.get("id") == "sync")
        guard = commit.get("if", "")
        assert "steps.regen-check.outputs.sync_gate == 'ok'" in guard, (
            "без гейта коммит ушёл бы при любом diff")
        assert "git commit" in commit["run"]

    def test_gate_violation_fails_reconcile(self, wf):
        steps = self._steps(wf)
        names = [s.get("name", "") for _, s in steps]
        assert any("sync gate rejected" in nm for nm in names), (
            "посторонний путь обязан ронять reconcile, а не только отключать merge")
        fail = next(s for _, s in steps if "sync gate rejected" in s.get("name", ""))
        assert fail.get("if", "").startswith("always()")
        assert "exit 1" in fail["run"]

    def test_generate_does_not_commit(self, wf):
        gen = next(s for _, s in self._steps(wf) if s.get("id") == "generate")
        assert "git commit" not in gen["run"], "генерация обязана оставить дерево грязным"
        assert 'echo "promoted=$PROMOTE" >> "$GITHUB_OUTPUT"' in gen["run"]

    def test_reports_are_checked_against_plan_before_sync(self, wf):
        steps = self._steps(wf)
        merge_i = next(i for i, s in steps if s.get("id") == "merge")
        gen_i = next(i for i, s in steps if s.get("id") == "generate")
        merge = steps[merge_i][1]
        assert "wave_report_gate.py" in merge["run"], (
            "отчёты обязаны сверяться с plan.json")
        assert "wave-plan-" in str(steps[3][1]), "исходный план должен скачиваться"
        # сверка — внутри шага merge, значит до generate/state sync
        assert merge_i < gen_i

    def test_plan_artifact_is_downloaded(self, wf):
        names = [s.get("name", "") for _, s in self._steps(wf)]
        assert "Download wave plan" in names
        dl = next(s for _, s in self._steps(wf) if s.get("name") == "Download wave plan")
        assert dl["with"]["name"] == "wave-plan-${{ github.run_number }}"
