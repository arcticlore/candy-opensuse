#!/usr/bin/env python3
"""test_schedule_gate.py — гейты unattended/ручного полного ребилда в update.yml.

Контекст: у окружения candy-production были required_reviewers, поэтому
scheduled-run вечно висел в `waiting` (конвейер мёртв). Ревьюер-гейт убран,
его заменяют машинные гейты, которые обязаны быть не weaker по безопасности:
  * schedule не может включить --force (никакого unattended полного ребилда);
  * ручной --force требует workflow_dispatch + двух подтверждений + allowlist;
  * emergency brake (CANDY_PUBLISH_PAUSE) остаётся ПЕРВЫМ.
"""
import os

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github/workflows/update.yml")


@pytest.fixture(scope="module")
def wf_text():
    with open(WF, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def wf(wf_text):
    return yaml.safe_load(wf_text)


def _all_jobs(wf):
    return wf["jobs"]


@pytest.fixture(scope="module")
def pipeline(wf):
    """Job, несущий гейты (имя менялось: pipeline -> plan)."""
    for name, job in _all_jobs(wf).items():
        if any(s.get("name", "").startswith("Scope check") for s in job["steps"]):
            return job
    raise AssertionError("не найден job с шагом 'Scope check'")


@pytest.fixture(scope="module")
def scope_run(wf):
    """Тело shell-скрипта шага 'Scope check'."""
    for job in _all_jobs(wf).values():
        for step in job["steps"]:
            if step.get("name", "").startswith("Scope check"):
                return step["run"]
    raise AssertionError("не найден шаг 'Scope check'")


def test_no_required_reviewer_environment(wf, pipeline):
    """`environment` с required_reviewers блокирует schedule — его быть не должно."""
    for name, job in _all_jobs(wf).items():
        assert "environment" not in job, (
            f"job {name} не должен зависеть от окружения с reviewer-гейтом: "
            "scheduled-run снова зависнет в waiting")
    env_rules = wf.get("env", {})
    assert "MAINTAINER_ALLOWLIST" in env_rules, (
        "allowlist актора для полного ребилда должен быть объявлен явно")


def test_schedule_cron_and_dispatch_present(wf):
    triggers = wf[True] if True in wf else wf["on"]
    assert "schedule" in triggers and triggers["schedule"], "cron-событие потеряно"
    assert triggers["schedule"][0]["cron"].strip() == "17 3 * * *"
    assert "workflow_dispatch" in triggers


def test_pause_brake_is_first(scope_run):
    body = [ln.strip() for ln in scope_run.splitlines() if ln.strip()]
    brake = next(i for i, ln in enumerate(body) if "CANDY_PUBLISH_PAUSE" in ln
                 and "= \"true\"" in ln)
    scope = next(i for i, ln in enumerate(body) if ln == "set -euo pipefail")
    force = next(i for i, ln in enumerate(body) if "ABORT: full_rebuild невозможен" in ln)
    assert scope < brake < force, (
        "emergency brake обязан срабатывать раньше любых проверок ребилда")


def test_force_forbidden_outside_workflow_dispatch(scope_run):
    assert "EVENT_NAME" in scope_run, "событие должно попадать в scope-check"
    assert '!= "workflow_dispatch"' in scope_run, (
        "нет гейта: --force вне workflow_dispatch обязан быть запрещён")


def test_force_requires_actor_allowlist(scope_run):
    assert "MAINTAINER_ALLOWLIST" in scope_run
    assert "ABORT: actor=" in scope_run, (
        "нет гейта: actor вне allowlist обязан блокировать полный ребилд")


def test_force_requires_double_confirm(scope_run):
    assert 'CONFIRM_FULL" != "true"' in scope_run, (
        "нет гейта: full_rebuild без confirm_full_rebuild обязан падать")


def test_force_flag_never_unconditional(scope_run):
    """FORCE_FLAG обязан выводиться только из FORCE, иначе --force может утечь."""
    lines = [ln.strip() for ln in scope_run.splitlines()]
    flag_out = next(ln for ln in lines if ln.startswith('echo "force_flag='))
    # --force выставляется ТОЛЬКО при FORCE=true, иначе переменная пуста.
    set_flag = next(ln for ln in lines if ln.startswith('[ "$FORCE" = "true" ]'))
    assert '--force' in set_flag, "force_flag должен выставляться только из --force при FORCE=true"
    assert flag_out.endswith('echo "force_flag=$FORCE_FLAG" >> "$GITHUB_OUTPUT"')
    assert "--force" not in scope_run.replace(set_flag, ""), \
        "--force не должен появляться в workflow вне этой строки"


def test_project_allowlists_kept(scope_run):
    assert "arcticlore/candy-opensuse" in scope_run
    assert "arcticlore/candy-opensuse-pilot" in scope_run
    assert "вне allowlist" in scope_run


def test_max_wave_bounded(scope_run):
    assert '-ge 1' in scope_run or '-lt 1' in scope_run, (
        "max_wave должен валидироваться, иначе пустой scope-check")

class TestWaveShape:
    """Волна не должна теряться из-за одного долгого build.

    Контекст: run 36971434567 отменили на общем таймауте 320 минут — четыре
    пакета, ушедшие в stable, потеряли результат вместе с ним. Эти тезы
    фиксируют требования к оркестровке: независимые package-джобы, потолок
    ожидания выше бюджета COPR, deferred вместо cancel и state-гейт, который
    не зависит от успеха пакетов.
    """

    def test_packages_are_independent_matrix_with_cap(self, wf):
        pkg = wf["jobs"]["package"]
        strat = pkg["strategy"]
        assert strat["fail-fast"] is False, (
            "один упавший пакет не должен отменять соседей по волне")
        assert strat["max-parallel"] == 3, (
            "параллелизм ограничен 3 активными сборками")
        assert "pkg" in strat["matrix"], "пакеты должны быть элементами матрицы"

    def test_job_timeout_allows_copr_to_outlast_github(self, wf):
        """Потолок job'а обязан быть больше бюджета наблюдения за сборкой."""
        job = wf["jobs"]["package"]
        assert job["timeout-minutes"] >= 720, (
            "timeout короче типичной длительности COPR-сборки обрывает волну")

    def test_no_legacy_single_pipeline_job(self, wf):
        assert "pipeline" not in wf["jobs"], (
            "монолитный job, где один таймаут съедает всю волну, должен быть "
            "разобран на независимые джобы")

    def test_per_package_report_is_written(self, wf):
        steps = wf["jobs"]["package"]["steps"]
        exec_step = next(s for s in steps if s.get("id") == "exec")
        assert "--report-path" in exec_step["run"], (
            "результат пакета должен попадать в отдельный отчёт, иначе таймаут "
            "соседнего пакета его съест")
        assert "--only" in exec_step["run"], (
            "per-package запуск обязателен, иначе джобы дублируют друг друга")

    def test_per_package_plan_is_not_truncated_before_filter(self, wf):
        """--max-wave обрезает план ДО --only: max-wave=1 убил бы хвост матрицы."""
        steps = wf["jobs"]["package"]["steps"]
        exec_step = next(s for s in steps if s.get("id") == "exec")
        assert "--max-wave 1 " not in exec_step["run"], (
            "max-wave=1 в per-package job оставит пакеты из хвоста плана "
            "с пустым планом")

    def test_deferred_is_not_success(self, wf):
        steps = wf["jobs"]["package"]["steps"]
        concl = next(s for s in steps
                     if s.get("name", "").startswith("Package conclusion"))
        assert "deferred" in concl["run"], (
            "deferred обязан быть явным исходом")
        assert concl.get("if") == "always()", (
            "исход пакета должен фиксироваться даже при таймауте")

    def test_reconcile_and_finalize_run_even_on_failure(self, wf):
        """Гейт не должен теряться из-за таймаута/провала одного пакета."""
        for name in ("reconcile", "finalize"):
            assert wf["jobs"][name].get("if") == "always()", (
                f"job {name} обязан выполняться always(): иначе провал одного "
                "долгого build'а съест и гейт, и sync")

    def test_state_sync_limited_to_promoted(self, wf):
        steps = wf["jobs"]["reconcile"]["steps"]
        sync = next(s for s in steps
                    if s.get("name", "").startswith("Sync state"))
        assert '"promoted"' in sync["run"], (
            "state/SPECS синхронизируются только для фактически опубликованных "
            "пакетов: deferred или 3/4 в репозиторий попадать не должны")

    def test_concurrency_is_run_level(self, wf):
        """Сериализация должна быть на весь run, иначе две волны спорят за chroot."""
        assert wf["concurrency"]["group"] == "opensuse-auto-publish"
        assert wf["concurrency"]["cancel-in-progress"] is False, (
            "текущая волна должна дорабатываться, а не отменяться новой")
