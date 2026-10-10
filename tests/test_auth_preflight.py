#!/usr/bin/env python3
"""test_auth_preflight.py — отдельный fail-fast preflight бот-токена.

Контекст: CANDY_BOT_TOKEN оказался невалидным и это всплыло только на шаге
reconcile — после всех сборок. Внутренний job bot-auth в update.yml заперт
тем, что CANDY_PUBLISH_PAUSE блокирует plan: проверку токена нельзя прогнать,
не запустив publish. auth-preflight.yml отвечает на это отдельным запуском по
workflow_dispatch: ГИТ/секрет проверяются, публикации не происходит.

Обязательства регрессий:
  * триггер только workflow_dispatch (никаких schedule/PR → никаких сборок);
  * секрет берётся именно из secrets.CANDY_BOT_TOKEN;
  * есть все пять проверок (1..5 из задания);
  * fail-closed: никакого continue-on-error, есть exit 1;
  * токен и credential-URL не утекают в лог (нет set -x, нет echo токена);
  * никаких COPR/OBS/сборочных вызовов и создания настоящих веток/PR.
"""
import os
import re
import subprocess
import tempfile

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github/workflows/auth-preflight.yml")


@pytest.fixture(scope="module")
def wf():
    with open(WF, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="module")
def job(wf):
    return wf["jobs"]["bot-auth"]


@pytest.fixture(scope="module")
def verify(job):
    return next(s for s in job["steps"] if s.get("id") == "verify")


@pytest.fixture(scope="module")
def run(verify):
    return verify["run"]


class TestWorkflowIsIsolatedFromPublish:
    """Проверка обязана идти отдельно от публикации и сборок."""

    def test_triggers_are_dispatch_only(self, wf):
        triggers = wf[True] if True in wf else wf["on"]
        assert "workflow_dispatch" in triggers, "запуск по требованию потерян"
        assert "schedule" not in triggers, "pre-flight не должен подниматься по расписанию"
        assert "pull_request" not in triggers, "pull_request запустил бы workflow на PR"
        assert "push" not in triggers, "push запускал бы preflight на каждый коммит"

    def test_expected_login_input_is_present(self, wf):
        triggers = wf[True] if True in wf else wf["on"]
        inputs = triggers["workflow_dispatch"].get("inputs", {})
        assert "expected_login" in inputs, (
            "ожидаемый login должен быть параметром, а не молча любым непустым")
        assert inputs["expected_login"].get("default") == "arcticlore", (
            "все исторические bot-PR созданы arcticlore")

    def test_workflow_has_no_write_permission(self, wf):
        assert wf.get("permissions") == {"contents": "read"}, (
            "сам preflight не должен получать write-права")

    def test_no_copr_or_obs_calls(self, run):
        for needle in ("copr", "COPR", "obs ", "OBS", "auto-publish", "auto_publish",
                       "builds/", "--recovery", "workflow_run"):
            assert needle not in run, f"в preflight не должно быть `{needle}`"

    def test_no_real_branch_or_pr_creation(self, run):
        """Только dry-run; ls-remote подтверждает отсутствие ветки."""
        assert "git push --dry-run" in run, "настоящий push запрещён"
        assert re.search(r"git push(?! --dry-run)", run) is None, (
            "найден git push без --dry-run")
        assert "gh pr create" not in run and "pulls" not in run
        assert "git ls-remote" in run, "нет подтверждения, что ветка не появилась"


class TestChecksPresent:
    """Пять проверок из задания — по одной на шаг."""

    def test_check1_get_user(self, run):
        assert "$API/user" in run
        assert "HTTP" in run and "200" in run
        assert "login" in run

    def test_check2_get_repo(self, run):
        assert "$API/repos/$REPO" in run
        assert re.search(r'CODE_REPO\s*=\s*"\$\{CODE_REPO:-000\}"', run), (
            "HTTP-код /repos обязан попадать в отчёт даже при транспортной ошибке")

    def test_check3_permissions_push(self, run):
        assert '"permissions"' in run
        assert "push" in run
        assert 'fail "permissions.push=' in run, "push != true обязан ронять запуск"

    def test_check4_push_dry_run_unique_ref(self, wf, job, verify):
        run = verify["run"]
        assert "git push --dry-run" in run
        assert "refs/heads/${PROBE_REF}" in run
        assert "candy-bot/auth-preflight-*" in str(job) or "auth-preflight-" in str(
            verify["env"]["PROBE_REF"]), "ref обязан быть уникальным для запуска"
        assert "run_id" in str(verify["env"]["PROBE_REF"]) and "run_attempt" in str(
            verify["env"]["PROBE_REF"]), "ref без run_id/run_attempt может пересечься"

    def test_check5_ls_remote_confirms_no_branch(self, run):
        assert "git ls-remote" in run
        assert '[ -z "$FOUND" ] || fail' in run, (
            "существующая ветка — это провал: dry-run что-то создал")
        assert "auth-preflight-*" in run, "нет проверки, что чужих веток тоже нет"

    def test_all_five_are_sequenced_before_report(self, run):
        # Берём строки самих проверок (не комментарии к секциям).
        order = [run.index(n) for n in ('"$API/user"',
                                        '[ "$CODE_REPO" = "200" ]',
                                        '[ "$PUSH" = "true" ]',
                                        "git push --dry-run",
                                        "git ls-remote")]
        assert order == sorted(order), "проверки должны идти в заданном порядке"


class TestFailClosed:
    """Неопределённость авторизации = провал, а не warning."""

    def test_no_continue_on_error(self, job, verify):
        assert not job.get("continue-on-error")
        assert not verify.get("continue-on-error"), (
            "continue-on-error превратил бы провал токена в зелёный прогон")

    def test_fail_helper_exits_nonzero(self, run):
        assert "fail() {" in run and "exit 1" in run
        assert "::error::" in run, "провал обязан быть виден в логе"

    def test_empty_token_is_fatal(self, run):
        assert 'fail "CANDY_BOT_TOKEN пуст"' in run, (
            "отсутствующий секрет обязан ронять проверку, а не пропускать её")

    def test_shell_tracing_disabled_and_strict_mode(self, run):
        assert "set -eu" in run, "строгий режим обязателен"
        assert "set +x" in run, "shell tracing должен быть выключен явно"
        assert "set -x" not in run, "set -x раскрыл бы токен"

    def test_no_declared_timeout_bypass(self, job):
        assert job.get("timeout-minutes", 0) <= 10, (
            "pre-flight обязан укладываться в считанные минуты")


class TestSecretNeverLeaked:
    """Ни токен, ни Authorization-заголовок, ни credential-URL."""

    def test_token_comes_from_candy_bot_secret(self, verify):
        assert verify["env"]["BOT_TOKEN"] == "${{ secrets.CANDY_BOT_TOKEN }}", (
            "проверять нужно секрет, которым откроется настоящий bot-PR")

    def test_no_echo_of_token(self, run):
        for i, line in enumerate(run.splitlines(), 1):
            stripped = line.strip()
            assert not re.search(r"\b(echo|printf|cat|tee|env|printenv)\b[^\n]*BOT_TOKEN",
                                 stripped), f"токен уходит в лог, строка {i}: {stripped}"
            assert "Authorization:" not in stripped or "echo" not in stripped, (
                f"заголовок Authorization печатается, строка {i}")

    def test_token_output_is_masked(self, run):
        assert "mask()" in run and "replace(t,'***')" in run, (
            "вывод git/curl обязан проходить через маску")
        assert "https://***@github.com" in run, "credential-URL не замаскирован"

    def test_checkout_does_not_persist_credentials(self, job):
        co = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
        assert co["with"].get("persist-credentials") is False, (
            "credentials вшитые в git config — лишний вектор утечки")

    def test_remote_credentials_are_dropped_afterwards(self, run):
        assert 'git remote set-url origin "https://github.com/${REPO}.git"' in run, (
            "токен обязан быть убран из remote после проверки")


class TestRunBlockIsValid:
    def test_bash_n(self):
        with open(WF, encoding="utf-8") as fh:
            wf = yaml.safe_load(fh)
        run = next(s for s in wf["jobs"]["bot-auth"]["steps"] if s.get("id") == "verify")["run"]
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(run)
            path = fh.name
        try:
            res = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
        finally:
            os.unlink(path)
        assert res.returncode == 0, res.stderr


class TestLocalFailClosedBehaviour:
    """Прогоняем сам run-блок локально: пустой и фейковый токен обязаны ронять."""

    @staticmethod
    def _script():
        with open(WF, encoding="utf-8") as fh:
            wf = yaml.safe_load(fh)
        return next(s for s in wf["jobs"]["bot-auth"]["steps"] if s.get("id") == "verify")["run"]

    @staticmethod
    def _run(tmp, script, token):
        sh = os.path.join(tmp, "auth.sh")
        with open(sh, "w", encoding="utf-8") as fh:
            fh.write(script)
        env = dict(os.environ,
                   BOT_TOKEN=token,
                   EXPECTED_LOGIN="arcticlore",
                   PROBE_REF="candy-bot/auth-preflight-1-1",
                   GITHUB_OUTPUT=os.path.join(tmp, "out"),
                   GITHUB_STEP_SUMMARY=os.path.join(tmp, "sum.md"),
                   GITHUB_REPOSITORY="arcticlore/candy-opensuse",
                   GITHUB_RUN_ID="LOCAL")
        return subprocess.run(["bash", sh], capture_output=True, text=True, env=env, timeout=60)

    def test_empty_token_fails(self, tmp_path):
        res = self._run(str(tmp_path), self._script(), "")
        assert res.returncode == 1, res.stdout + res.stderr
        assert "CANDY_BOT_TOKEN пуст" in res.stderr + res.stdout

    def test_invalid_token_fails_on_get_user(self, tmp_path):
        res = self._run(str(tmp_path), self._script(),
                        "ghp_thisisnotarealtoken00000000000000")
        # Сеть может быть недоступна — но провал обязателен в любом случае.
        assert res.returncode == 1, res.stdout + res.stderr
        assert "auth-preflight" in res.stderr + res.stdout

    def test_no_token_value_in_output(self, tmp_path):
        token = "ghp_thisisnotarealtoken00000000000000"
        res = self._run(str(tmp_path), self._script(), token)
        blob = res.stdout + res.stderr
        assert token not in blob, "значение токена утекло в лог"
