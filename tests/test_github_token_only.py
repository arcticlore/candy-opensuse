#!/usr/bin/env python3
"""test_github_token_only.py — отказ от личного PAT в пользу github.token.

Контекст: личный CANDY_BOT_TOKEN в Actions-секрете отозван/просрочен и это
всплыло только на шаге reconcile — ПОСЛЕ всех сборок. Решение: полностью
уйти на встроенный run-scoped GITHUB_TOKEN. Секрет при этом НЕ удаляется до
финального аудита, но ни один workflow его больше не читает.

Обязательства (по каждому пункту — отдельный тест-класс):
  * отсутствие CANDY_BOT_TOKEN references во всех workflow;
  * явный dispatch validate.yml, т.к. PR от GITHUB_TOKEN сам CI не триггерит;
  * привязка к exact head SHA PR (а не к ветке вообще);
  * наблюдение required checks до auto-merge и фактический merged verdict;
  * запрет прямого push в master (только candy-bot/*);
  * fail-closed на любом отказе API/чек-листа/таймауте;
  * при CANDY_PUBLISH_PAUSE=true путь к package/build недоступен.
"""
import json
import os
import re
import subprocess
import tempfile

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF_DIR = os.path.join(ROOT, ".github/workflows")
CHECKS_FILE = os.path.join(ROOT, "ci/required-checks.json")

# Ровно то, что защищает ветка master (см. ci/required-checks.json).
EXPECTED_REQUIRED_CHECKS = [
    "decommission",
    "drift-check",
    "tests",
    "opensuse-specs (tumbleweed, registry.opensuse.org/opensuse/tumbleweed)",
    "opensuse-specs (leap-16.0, opensuse/leap:16.0)",
    "waiters",
]

WORKFLOWS = sorted(
    os.path.join(WF_DIR, n) for n in os.listdir(WF_DIR) if n.endswith((".yml", ".yaml"))
)


def load(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def run_blocks(wf):
    for job in (wf.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if isinstance(step.get("run"), str):
                yield step


def steps_named(wf, *needles):
    for job in (wf.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            name = step.get("name", "")
            if all(n in name for n in needles):
                yield step


def triggers(wf):
    return wf[True] if True in wf else wf["on"]


# ---------------------------------------------------------------------------
# 1. Ни один workflow не читает личный PAT
# ---------------------------------------------------------------------------
class TestNoPersonalTokenReferences:
    def test_no_candy_bot_token_in_any_workflow(self):
        offenders = [os.path.basename(p) for p in WORKFLOWS
                     if "CANDY_BOT_TOKEN" in text(p)]
        assert offenders == [], (
            f"личный PAT всё ещё читается: {offenders}")

    def test_no_pat_shaped_literals_anywhere(self):
        """Ни строки, похожей на PAT, ни fallback-цепочек на него."""
        pat = re.compile(r"ghp_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,}")
        for p in WORKFLOWS:
            body = text(p)
            assert not pat.search(body), f"похоже на литеральный PAT: {p}"
            assert "x-access-token:" not in body or "GITHUB_TOKEN" in body or \
                "BOT_TOKEN" in body, f"credentialed URL без встроенного токена: {p}"

    def test_no_token_fallback_chains(self):
        """`secrets.X || secrets.Y` — признак «запасного» личного токена."""
        for p in WORKFLOWS:
            body = text(p)
            assert not re.search(r"secrets\.\w+\s*\|\|\s*secrets\.", body), (
                f"fallback-цепочка секретов осталась в {os.path.basename(p)}")

    def test_no_deploy_key_or_ssh_auth(self):
        for p in WORKFLOWS:
            body = text(p)
            for needle in ("id_rsa", "ssh-agent", ".pem", "SSH_PRIVATE_KEY",
                           "webfactory/ssh-agent"):
                assert needle not in body, f"{os.path.basename(p)}: {needle}"


# ---------------------------------------------------------------------------
# 2. Права workflow — минимальные и достаточные
# ---------------------------------------------------------------------------
class TestMinimalPermissions:
    def test_update_has_needed_write_permissions(self):
        wf = load(os.path.join(WF_DIR, "update.yml"))
        perms = wf.get("permissions") or {}
        for need in ("contents", "pull-requests", "actions"):
            assert perms.get(need) == "write", (
                f"update.yml: {need}: write обязателен (push/PR/dispatch)")
        assert "write-all" not in str(perms), "write-all — не minimal permissions"

    def test_pr_producing_workflows_can_dispatch_validate(self):
        for name in ("sync-specs.yml", "logrotate.yml"):
            wf = load(os.path.join(WF_DIR, name))
            perms = wf.get("permissions") or {}
            for need in ("contents", "pull-requests", "actions"):
                assert perms.get(need) == "write", (
                    f"{name}: {need}: write обязателен")

    def test_auth_preflight_is_least_privilege(self):
        """preflight не создаёт PR и ничего не dispatch'ит."""
        wf = load(os.path.join(WF_DIR, "auth-preflight.yml"))
        assert wf.get("permissions") == {"contents": "write"}, (
            "preflight должен иметь только contents: write")

    def test_validate_stays_read_only(self):
        wf = load(os.path.join(WF_DIR, "validate.yml"))
        assert wf.get("permissions") == {"contents": "read"}, (
            "validate.yml ничего не пишет")


# ---------------------------------------------------------------------------
# 3. Явный dispatch validate.yml (GITHUB_TOKEN-PR сам CI не триггерит)
# ---------------------------------------------------------------------------
class TestExplicitValidateDispatch:
    def test_validate_accepts_manual_dispatch(self):
        wf = load(os.path.join(WF_DIR, "validate.yml"))
        assert "workflow_dispatch" in triggers(wf), (
            "нет workflow_dispatch — dispatch validate.yml невозможен")

    @pytest.mark.parametrize("name", ["update.yml", "sync-specs.yml", "logrotate.yml"])
    def test_each_pr_producer_dispatches_validate(self, name):
        wf = load(os.path.join(WF_DIR, name))
        found = [s for s in run_blocks(wf) if "merge_gate.py" in s["run"]
                 and "dispatch" in s["run"]]
        assert found, f"{name}: после создания PR нет явного dispatch validate.yml"
        run = found[0]["run"]
        assert "--workflow validate.yml" in run, (
            f"{name}: dispatch должен идти на validate.yml")
        assert "--branch" in run and "--head-sha" in run, (
            f"{name}: dispatch обязан быть привязан к ветке И head SHA")
        assert "--repo" in run

    def test_dispatch_is_after_pr_creation_in_update(self):
        wf = load(os.path.join(WF_DIR, "update.yml"))
        names = [s.get("name", "") for s in wf["jobs"]["reconcile"]["steps"]]
        sync_i = next(i for i, n in enumerate(names) if n.startswith("Commit state/SPECS"))
        disp_i = next(i for i, n in enumerate(names) if "Dispatch validate.yml" in n)
        assert sync_i < disp_i, "dispatch раньше создания PR"

    def test_dispatch_step_requires_successful_sync(self):
        wf = load(os.path.join(WF_DIR, "update.yml"))
        step = next(steps_named(wf, "Dispatch validate.yml"))
        guard = step.get("if", "")
        assert "steps.sync.outputs.pr_url != ''" in guard, (
            "dispatch без PR означает гонку на несуществующую ветку")


# ---------------------------------------------------------------------------
# 4. Exact head SHA — во всех точках привязки
# ---------------------------------------------------------------------------
class TestExactHeadSha:
    def test_sync_publishes_head_sha(self):
        wf = load(os.path.join(WF_DIR, "update.yml"))
        sync = next(steps_named(wf, "Commit state/SPECS"))
        run = sync["run"]
        assert "--json headRefOid" in run, (
            "head SHA обязан браться из самого PR, а не считаться локально")
        assert 'echo "head_sha=$HEAD_SHA"' in run
        assert '} >> "$GITHUB_OUTPUT"' in run
        # и подтверждаться фактической головой ветки
        assert "git ls-remote origin" in run
        assert '"$REMOTE_HEAD" != "$HEAD_SHA"' in run

    def test_downstream_steps_receive_that_sha(self):
        wf = load(os.path.join(WF_DIR, "update.yml"))
        for needle in ("Dispatch validate.yml", "Wait required checks"):
            step = next(steps_named(wf, needle))
            assert "--head-sha" in step["run"], f"{needle}: нет --head-sha"
            assert "${{ steps.sync.outputs.head_sha }}" in str(step.get("env")), (
                f"{needle}: head SHA должен приходить из шага sync")

    def test_merge_gate_dispatch_refuses_non_bot_branch(self, mg):
        with pytest.raises(mg.GateError):
            mg.dispatch_workflow("validate.yml", branch="master", head_sha="a" * 40,
                                 repo="o/r", gh="gh")

    def test_merge_gate_dispatch_refuses_missing_sha(self, mg):
        with pytest.raises(mg.GateError):
            mg.dispatch_workflow("validate.yml", branch="candy-bot/x",
                                 head_sha="", repo="o/r", gh="gh")

    def test_merge_gate_dispatch_detects_branch_drift(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "remote_branch_head",
                            lambda branch, repo, gh="gh": "b" * 40)
        with pytest.raises(mg.GateError, match="не совпала"):
            mg.dispatch_workflow("validate.yml", branch="candy-bot/x",
                                 head_sha="a" * 40, repo="o/r", gh="gh")

    def test_merge_gate_dispatch_checks_sha_again_after_dispatch(self, mg, monkeypatch):
        """Голова ветки не должна уйти после dispatch — иначе checks на другом SHA."""
        seen = {"calls": 0}

        def fake_head(branch, repo, gh="gh"):
            seen["calls"] += 1
            return "a" * 40 if seen["calls"] == 1 else "b" * 40

        monkeypatch.setattr(mg, "remote_branch_head", fake_head)
        monkeypatch.setattr(mg, "_run", lambda *a, **k: "")
        with pytest.raises(mg.GateError, match="сменилась"):
            mg.dispatch_workflow("validate.yml", branch="candy-bot/x",
                                 head_sha="a" * 40, repo="o/r", gh="gh")


# ---------------------------------------------------------------------------
# 5. Наблюдение required checks до auto-merge
# ---------------------------------------------------------------------------
class TestRequiredCheckObservation:
    def test_required_checks_file_matches_branch_protection(self):
        data = json.loads(open(CHECKS_FILE, encoding="utf-8").read())
        assert data["contexts"] == EXPECTED_REQUIRED_CHECKS, (
            "ci/required-checks.json разошёлся с protection ветки master")
        assert data["strict"] is True

    def test_update_waits_on_checks_before_auto_merge(self):
        wf = load(os.path.join(WF_DIR, "update.yml"))
        names = [s.get("name", "") for s in wf["jobs"]["reconcile"]["steps"]]
        wait_i = next(i for i, n in enumerate(names) if "Wait required checks" in n)
        merge_i = next(i for i, n in enumerate(names) if "Enable auto-merge" in n)
        assert wait_i < merge_i, "auto-merge раньше ожидания checks"

    def test_auto_merge_is_blocked_without_green_checks(self):
        wf = load(os.path.join(WF_DIR, "update.yml"))
        step = next(steps_named(wf, "Enable auto-merge"))
        guard = step.get("if", "")
        assert "steps.wait-checks.outcome == 'success'" in guard, (
            "auto-merge без доказанных checks — обход required проверок")
        assert "steps.regen-check.outputs.regen_gate == 'ok'" in guard

    def test_wait_step_reads_the_checks_file(self):
        wf = load(os.path.join(WF_DIR, "update.yml"))
        step = next(steps_named(wf, "Wait required checks"))
        assert "merge_gate.py wait-checks" in step["run"]
        assert "ci/required-checks.json" in step["run"]
        assert "--timeout-min" in step["run"], "без таймаута ожидание вечно"

    def test_merged_verdict_is_observed(self):
        wf = load(os.path.join(WF_DIR, "update.yml"))
        step = next(steps_named(wf, "Observe until merged"))
        assert "merge_gate.py observe-merged" in step["run"]
        assert "--pr-url" in step["run"]
        # и наблюдение идёт ПОСЛЕ включения автомерджа
        names = [s.get("name", "") for s in wf["jobs"]["reconcile"]["steps"]]
        assert names.index("Enable auto-merge after green checks") < \
            names.index("Observe until merged")

    def test_merge_gate_cli_fails_on_gate_error(self):
        res = subprocess.run(
            ["python3", os.path.join(ROOT, "bin/merge_gate.py"), "wait-checks",
             "--repo", "o/r", "--head-sha", "abc",
             "--checks-file", "/definitely/missing.json"],
            capture_output=True, text=True, cwd=ROOT, timeout=60)
        assert res.returncode == 1, res.stdout + res.stderr
        assert "::error::merge-gate:" in res.stderr

    def test_evaluate_marks_missing_and_pending(self, mg):
        required = ["a", "b", "c"]
        runs = [{"name": "a", "head_sha": "s", "conclusion": "success"},
                {"name": "b", "head_sha": "s", "conclusion": "in_progress"}]
        ready, report = mg.evaluate(runs, required)
        assert ready is False
        assert any("c: MISSING" in line for line in report)
        assert any("b: PENDING" in line for line in report)
        assert any("a: ok" in line for line in report)

    def test_wait_reports_success_only_when_all_green(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "check_runs",
                            lambda repo, sha, gh="gh": [
                                {"name": c, "head_sha": sha, "conclusion": "success"}
                                for c in EXPECTED_REQUIRED_CHECKS])
        report = mg.wait_for_checks("o/r", "abc", EXPECTED_REQUIRED_CHECKS,
                                    timeout_min=1, sleep=lambda s: None)
        assert len(report) == len(EXPECTED_REQUIRED_CHECKS)

    def test_wait_times_out_when_check_missing(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "check_runs", lambda repo, sha, gh="gh": [])
        clock = _Clock()
        with pytest.raises(mg.GateError, match="таймаут"):
            mg.wait_for_checks("o/r", "abc", EXPECTED_REQUIRED_CHECKS,
                               timeout_min=0.01, poll_s=1, sleep=clock.sleep,
                               clock=clock)

    def test_wait_fails_fast_on_failed_required_check(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "check_runs", lambda repo, sha, gh="gh": [
            {"name": EXPECTED_REQUIRED_CHECKS[0], "head_sha": sha,
             "conclusion": "failure"}])
        clock = _Clock()
        with pytest.raises(mg.GateError, match="отказ"):
            mg.wait_for_checks("o/r", "abc", EXPECTED_REQUIRED_CHECKS,
                               timeout_min=10, poll_s=1, sleep=clock.sleep,
                               clock=clock)


# ---------------------------------------------------------------------------
# 6. Fail-closed на любом отказе API
# ---------------------------------------------------------------------------
class TestFailClosedApi:
    def test_nonzero_exit_is_gate_error(self, mg, monkeypatch):
        def boom(*argv, **kwargs):
            raise mg.GateError("rc=1")
        monkeypatch.setattr(mg, "_run", boom)
        with pytest.raises(mg.GateError):
            mg.gh_api("repos/o/r")

    def test_malformed_json_is_gate_error(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "_run", lambda *a, **k: "{not json")
        with pytest.raises(mg.GateError, match="JSON"):
            mg.gh_api("repos/o/r")

    def test_check_run_for_another_sha_is_gate_error(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "gh_api", lambda path, gh="gh": {"check_runs": [
            {"name": "tests", "head_sha": "different", "conclusion": "success"}]})
        with pytest.raises(mg.GateError, match="head_sha"):
            mg.check_runs("o/r", "abc")

    def test_check_runs_payload_shape_is_validated(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "gh_api", lambda path, gh="gh": {"unexpected": []})
        with pytest.raises(mg.GateError):
            mg.check_runs("o/r", "abc")

    def test_pr_state_is_required(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "gh_json", lambda *a, **k: {})
        with pytest.raises(mg.GateError):
            mg.pr_state("https://x")

    def test_load_required_checks_rejects_empty(self, mg, tmp_path):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({"contexts": []}), encoding="utf-8")
        with pytest.raises(mg.GateError):
            mg.load_required_checks(p)

    def test_closed_unmerged_is_gate_error(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "pr_state", lambda url, gh="gh": "CLOSED")
        with pytest.raises(mg.GateError, match="без merge"):
            mg.observe_merged("https://x", timeout_min=1,
                              sleep=lambda s: None, clock=_Clock())

    def test_observe_times_out_while_still_open(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "pr_state", lambda url, gh="gh": "OPEN")
        clock = _Clock()
        with pytest.raises(mg.GateError, match="таймаут"):
            mg.observe_merged("https://x", timeout_min=0.01, poll_s=1,
                              sleep=clock.sleep, clock=clock)

    def test_observe_returns_merged(self, mg, monkeypatch):
        monkeypatch.setattr(mg, "pr_state", lambda url, gh="gh": "MERGED")
        assert mg.observe_merged("https://x", timeout_min=1,
                                 sleep=lambda s: None, clock=_Clock()) == "MERGED"

    @pytest.mark.parametrize("name", ["update.yml", "sync-specs.yml",
                                      "logrotate.yml", "auth-preflight.yml"])
    def test_gate_steps_never_swallows_failure(self, name):
        wf = load(os.path.join(WF_DIR, name))
        for step in wf["jobs"].get("bot-auth", {}).get("steps", []):
            assert not step.get("continue-on-error"), name
        assert not any(j.get("continue-on-error") for j in wf["jobs"].values()), (
            f"{name}: continue-on-error отключает fail-closed")


# ---------------------------------------------------------------------------
# 7. Запрет прямого push в master
# ---------------------------------------------------------------------------
class TestNoDirectMasterPush:
    @staticmethod
    def _push_targets(run):
        return re.findall(r"git\s+push\s+[^\n]*", run)

    def test_no_literal_push_to_master_anywhere(self):
        for p in WORKFLOWS:
            for block in run_blocks(load(p)):
                for cmd in self._push_targets(block["run"]):
                    assert not re.search(r"\bmaster\b", cmd), (
                        f"{os.path.basename(p)}: прямой push в master: {cmd.strip()}")
                    assert "HEAD:master" not in cmd
                    assert "refs/heads/master" not in cmd

    def test_update_push_guard_rejects_non_bot_branch(self):
        wf = load(os.path.join(WF_DIR, "update.yml"))
        run = next(steps_named(wf, "Commit state/SPECS"))["run"]
        assert 'case "$BRANCH" in' in run
        assert "candy-bot/*)" in run
        assert "вне префикса candy-bot/*" in run

    def test_sync_specs_refuses_master_in_direct_push_path(self):
        run = text(os.path.join(WF_DIR, "sync-specs.yml"))
        assert "master|refs/heads/master)" in run, "ветка master не выделена в PR-путь"
        assert "прямой push в master запрещён" in run, (
            "нет второго рубежа на случай, если TARGET_REF всё же укажет master")

    def test_logrotate_guard_present(self):
        run = text(os.path.join(WF_DIR, "logrotate.yml"))
        assert 'case "$BRANCH" in' in run and "candy-bot/*)" in run

    def test_bot_pr_is_the_only_path_to_master(self):
        """Каждый путь, ведущий в master, проходит через gh pr create --base master."""
        for p in WORKFLOWS:
            wf = load(p)
            for block in run_blocks(wf):
                body = block["run"]
                if re.search(r"git\s+push[^\n]*master", body):
                    assert "gh pr create --base master" in body, (
                        f"{os.path.basename(p)}: push к master без reviewable PR")


# ---------------------------------------------------------------------------
# 8. При CANDY_PUBLISH_PAUSE=true путь к package/build закрыт
# ---------------------------------------------------------------------------
class TestPausedMeansNoPackageOrBuild:
    def test_pause_gate_reads_repo_variable_and_aborts(self, update_wf):
        wf = update_wf
        plan = wf["jobs"]["plan"]
        scope = next(s for s in plan["steps"]
                     if "CANDY_PUBLISH_PAUSE" in str(s.get("env", {})))
        run = scope["run"]
        assert 'CANDY_PUBLISH_PAUSE:-false}" = "true"' in run
        assert "ABORT: repo variable CANDY_PUBLISH_PAUSE=true" in run
        assert 'PREFLIGHT_ONLY}" != "true"' in run, (
            "preflight-only должен работать при pause=true")

    def test_pause_gate_precedes_the_plan(self, update_wf):
        wf = update_wf
        plan = wf["jobs"]["plan"]
        idx_scope = next(i for i, s in enumerate(plan["steps"])
                         if "CANDY_PUBLISH_PAUSE" in str(s.get("env", {})))
        idx_plan = next(i for i, s in enumerate(plan["steps"])
                        if "auto-publish.py plan" in s.get("run", ""))
        assert idx_scope < idx_plan, (
            "гейт должен срабатывать ДО построения плана, а не после")

    def test_only_package_job_can_submit(self, update_wf):
        wf = update_wf
        """Единственный submit-вызов живёт в package job'е."""
        holders = [name for name, job in wf["jobs"].items()
                   if any("auto-publish.py run" in (s.get("run") or "")
                          for s in job.get("steps") or [])]
        assert holders == ["package"], (
            f"вызов run вне package-задачи: {holders}")

    def test_package_requires_successful_plan_and_auth(self, update_wf):
        wf = update_wf
        pkg = wf["jobs"]["package"]
        assert "plan" in pkg["needs"] and "bot-auth" in pkg["needs"]
        guard = pkg.get("if", "")
        assert "needs.plan.result == 'success'" in guard
        assert "needs.bot-auth.result == 'success'" in guard
        assert "!= '[]'" in guard

    def test_aborted_plan_produces_no_packages(self, update_wf):
        """Пустой план → '[]' → пустая матрица → package job'ы пропускаются.

        Гейт срабатывает внутри job'а plan до шага, который эмитит packages:
        output остаётся пустым, и fallback `|| '[]'` даёт пустую матрицу.
        """
        wf = update_wf
        plan = wf["jobs"]["plan"]
        outputs = plan.get("outputs") or {}
        assert "packages" in outputs
        assert "|| '[]'" in str(outputs["packages"]), (
            "без fallback на '[]' упавший/пропущенный plan уронил бы fromJSON")
        matrix = wf["jobs"]["package"]["strategy"]["matrix"]
        assert matrix["pkg"] == "${{ fromJSON(needs.plan.outputs.packages) }}", (
            "матрица пакетов обязана строиться из plan-выхода, а не из чего-то ещё")


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------
class _Clock:
    """Управляемые clock/sleep для детерминированных таймаутов."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


@pytest.fixture(scope="module")
def update_wf():
    return load(os.path.join(WF_DIR, "update.yml"))


@pytest.fixture(scope="module")
def mg():
    import merge_gate
    return merge_gate
