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

    def test_missing_plan_with_zero_reports_is_a_failure(self, tmp_path):
        """Регрессия: ноль отчётов + отсутствующий plan.json = ПРОВАЛ.

        Раньше это считалось успехом, и волна могла «чисто» пройти сверку,
        которой фактически не было.
        """
        problems = report_gate.reconcile(str(tmp_path / "plan.json"),
                                         str(tmp_path / "logs/report-*.json"))
        assert problems, "отсутствующий план обязан быть ошибкой даже без отчётов"
        assert "не найден" in problems[0], problems

    def test_missing_plan_json_file_with_zero_reports_is_a_failure(self, tmp_path):
        """То же для явно переданного, но несуществующего пути."""
        (tmp_path / "logs").mkdir()
        problems = report_gate.reconcile(str(tmp_path / "logs/plan.json"),
                                         str(tmp_path / "logs/report-*.json"))
        assert problems and "не найден" in problems[0], problems

    def test_valid_empty_plan_with_zero_reports_passes(self, tmp_path):
        """Единственный допустимый «ноль отчётов»: packages=[] и plan_size=0."""
        _plan(tmp_path / "plan.json", [])
        assert report_gate.reconcile(str(tmp_path / "plan.json"),
                                     str(tmp_path / "logs/report-*.json")) == []

    @pytest.mark.parametrize("size,packages", [
        (1, []),                      # plan_size врёт про пустой список
        (0, [{"name": "dysk", "target": "3.7.1"}]),   # plan_size врёт про пакеты
    ])
    def test_inconsistent_plan_size_with_zero_reports_fails(self, tmp_path,
                                                            size, packages):
        _plan(tmp_path / "plan.json", packages)
        path = tmp_path / "plan.json"
        data = json.loads(path.read_text())
        data["plan_size"] = size
        path.write_text(json.dumps(data))
        problems = report_gate.reconcile(str(path),
                                         str(tmp_path / "logs/report-*.json"))
        assert problems, "битый plan_size не должен считаться валидным планом"

    def test_plan_without_plan_size_is_not_valid(self, tmp_path):
        path = tmp_path / "plan.json"
        path.write_text(json.dumps({"packages": []}))
        problems = report_gate.reconcile(str(path),
                                         str(tmp_path / "logs/report-*.json"))
        assert problems and "plan_size" in problems[0], problems

    def test_unparseable_plan_is_a_failure(self, tmp_path):
        path = tmp_path / "plan.json"
        path.write_text("{не json")
        problems = report_gate.reconcile(str(path),
                                         str(tmp_path / "logs/report-*.json"))
        assert problems and "JSON" in problems[0], problems

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
        assert any("gate rejected" in nm for nm in names), (
            "посторонний путь обязан ронять reconcile, а не только отключать merge")
        fail = next(s for _, s in steps if "gate rejected" in s.get("name", ""))
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

    def test_regen_fail_with_sync_pass_fails_reconcile_without_commit(self, wf):
        """Регрессия: regen=fail + sync=ok больше не даёт зелёный run.

        regen-check всегда выходил с кодом 0 (вердикт уходил в outputs), а
        commit-шаг смотрел только на sync_gate — поэтому провал regen-гейта
        оставался warn'ом в логе при ушедшем в ветку коммите.
        """
        steps = self._steps(wf)
        commit = next(s for _, s in steps if s.get("id") == "sync")
        guard = commit.get("if", "")
        assert "steps.regen-check.outputs.regen_gate == 'ok'" in guard, (
            "commit обязан требовать regen_gate=ok, иначе regen=fail коммитит")
        assert "steps.regen-check.outputs.sync_gate == 'ok'" in guard
        assert "git commit" in commit["run"]

        fail = next(s for _, s in steps if "gate rejected" in s.get("name", ""))
        fail_if = fail.get("if", "")
        assert "regen_gate == 'fail'" in fail_if, (
            "финальный fail-шаг обязан срабатывать и при regen=fail")
        assert "sync_gate == 'fail'" in fail_if
        assert "exit 1" in fail["run"]

        # гейт обязан публиковать оба вердикта
        gate = next(s for _, s in steps if s.get("id") == "regen-check")
        assert "regen_gate=$REGEN_GATE" in gate["run"]
        assert "sync_gate=$SYNC_GATE" in gate["run"]

    def test_pr_create_failure_fails_state_publication(self, wf):
        """Регрессия: падение `gh pr create` после push обязано быть красным.

        Раньше это был warning-only: ветка запушена, PR не создан, а reconcile
        оставался зелёным — state/SPECS не публиковались как ревьюируемые.
        """
        commit = next(s for _, s in self._steps(wf) if s.get("id") == "sync")
        run = commit["run"]
        assert "gh pr create" in run
        assert "WARN: PR не создан" not in run, (
            "ошибка создания PR не может быть warning-only")
        # ветка ошибки должна содержать ::error:: и выходить с ненулевым кодом
        tail = run.split("gh pr create", 1)[1]
        assert "else" in tail
        else_branch = tail.split("else", 1)[1]
        assert "::error::" in else_branch, "падение PR обязано быть видно в логе"
        assert "exit 1" in else_branch, (
            "state publication обязана завершаться красным при ошибке PR")
        assert "pr_url=" not in else_branch

    def test_plan_artifact_is_downloaded(self, wf):
        names = [s.get("name", "") for _, s in self._steps(wf)]
        assert "Download wave plan" in names
        dl = next(s for _, s in self._steps(wf) if s.get("name") == "Download wave plan")
        assert dl["with"]["name"] == "wave-plan-${{ github.run_number }}"
