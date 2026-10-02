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


@pytest.fixture(scope="module")
def pipeline(wf):
    return wf["jobs"]["pipeline"]


@pytest.fixture(scope="module")
def scope_run(wf):
    """Тело shell-скрипта шага 'Scope check'."""
    steps = wf["jobs"]["pipeline"]["steps"]
    step = next(s for s in steps if s.get("name", "").startswith("Scope check"))
    return step["run"]


def test_no_required_reviewer_environment(wf, pipeline):
    """`environment` с required_reviewers блокирует schedule — его быть не должно."""
    assert "environment" not in pipeline, (
        "job не должен зависеть от окружения с reviewer-гейтом: "
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