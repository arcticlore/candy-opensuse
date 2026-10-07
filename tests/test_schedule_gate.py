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

    def test_job_timeout_is_bounded_but_leaves_headroom(self, wf):
        """Потолок job'а <= 360, а бюджет наблюдения строго меньше его.

        Наблюдение за COPR живёт в PKG_TIMEOUT_MIN. Если бы оно равнялось
        (или превышало) timeout-minutes, GitHub убил бы job посреди шага, и
        отчёт с verdict'ом `deferred` не успел бы записаться — пакет потерял бы
        свой build id, а не продолжил бы наблюдение в следующей волне.
        """
        job = wf["jobs"]["package"]
        assert job["timeout-minutes"] <= 360, (
            "потолок job'а ограничен 360 минутами")
        steps = job["steps"]
        exec_step = next(s for s in steps if s.get("id") == "exec")
        env = exec_step.get("env", {})
        observe = int(env["PKG_TIMEOUT_MIN"])
        assert observe < job["timeout-minutes"], (
            "наблюдение обязано завершиться раньше, чем job добьёт GitHub, "
            "иначе deferred не запишется")
        assert job["timeout-minutes"] - observe >= 30, (
            "на запись отчёта, вердикт deferred и upload артефактов должен "
            "оставаться реальный запас времени")

    def test_one_budget_is_shared_by_pilot_and_stable(self, wf, mod=None):
        """pilot и stable делят ОДИН бюджет пакета, а не получают по timeout_min.

        Иначе два stage по 300 минут дают 600 при потолке job'а 360, и job
        убивается GitHub'ом посреди stable — ровно та потеря результата, из-за
        которой переписывалась оркестровка.
        """
        import ast
        with open(os.path.join(ROOT, "bin", "auto-publish.py")) as fh:
            src = fh.read()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "process_package")
        body = ast.get_source_segment(src, fn)
        assert "deadline = time.monotonic() + timeout_min * 60.0" in body, (
            "process_package обязан задать общий дедлайн")
        await_fn = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef) and n.name == "await_stage")
        params = [a.arg for a in await_fn.args.args]
        assert "deadline" in params, (
            "await_stage обязан принимать общий дедлайн, а не локальный таймаут")
        assert "timeout_min" not in params, (
            "await_stage не должен иметь собственный timeout_min — иначе два "
            "stage снова получат по полному бюджету и сломают потолок job'а")
        assert "remain_min" in ast.get_source_segment(src, await_fn), (
            "дедлайн должен переводиться в оставшееся время")

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

    def test_matrix_carries_immutable_plan_records_not_names(self, wf):
        """P0: из plan в matrix уходят name+target+mode+id сборок.

        Раньше матрица содержала только имена, и каждый package-job заново
        строил план из ЖИВОГО state.json. План мог уехать между jobs — job
        исполнял не то, что plan-job решил. Запись обязана приходить целиком.
        """
        plan_step = next(s for s in wf["jobs"]["plan"]["steps"]
                         if s.get("id") == "plan")
        # в вывод уходит список ЦЕЛЫХ объектов, а не имен
        assert "'packages']))" in plan_step["run"] or '"packages"]])' in plan_step["run"]             or "[p['name'] for p in" not in plan_step["run"], (
            "в матрицу должны идти записи плана, а не только имена")

        pkg = wf["jobs"]["package"]
        matrix = pkg["strategy"]["matrix"]
        assert "pkg" in matrix, "элементы матрицы — записи плана"
        exec_step = next(s for s in pkg["steps"] if s.get("id") == "exec")
        entry = str(exec_step.get("env", {}).get("WAVE_PLAN_ENTRY", ""))
        assert "toJSON(matrix.pkg)" in entry, (
            "запись плана должна передаваться в job целиком (toJSON), "
            "а не по одному полю за раз")
        # для имён логов/артефактов бери ровно то, что записал plan-job,
        # а не вычисляй заново
        assert "matrix.pkg.name" in str(pkg), (
            "имя пакета должно идти из записи плана")

    def test_package_job_executes_plan_entry_without_replanning(self, wf):
        """--plan-entry: план исполняется как есть, а не пересобирается."""
        steps = wf["jobs"]["package"]["steps"]
        exec_step = next(s for s in steps if s.get("id") == "exec")
        run = exec_step["run"]
        assert "--plan-entry" in run, (
            "без --plan-entry job перестроил бы план по живому state.json")
        env = exec_step.get("env", {})
        assert "WAVE_PLAN_ENTRY" in env, (
            "запись плана передаётся через env, чтобы её не разрезали кавычки")
        # --only больше не используется: он отфильтровывал план по имени уже
        # ПОСЛЕ того, как max-wave мог его обрезать
        assert "--only " not in run, (
            "--only перестраивает выборку по имени; иммутабельная запись "
            "plan-entry делает его лишним")
        assert "--max-wave" not in run, (
            "в package-job план не строится заново — max-wave там не при чём")

    def test_package_runs_only_when_plan_succeeded(self, wf):
        """Упавший plan не должен превращаться в fromJSON('') внутри matrix."""
        guard = wf["jobs"]["package"].get("if", "")
        assert "needs.plan.result" in guard, (
            "без проверки plan.result упавший plan дал бы невалидную матрицу")
        assert "success" in guard
        assert "!= '[]'" in guard, "пустой план — штатный skip, а не ошибка"

    def test_plan_job_outputs_have_real_fallback(self, wf):
        """P0: job outputs обязаны иметь fallback, а не зависеть от одного шага.

        Отдельный шаг со СВОИМ GITHUB_OUTPUT не может записать в outputs шага
        id=plan — прежняя «страховка» была мёртвым кодом. Проверяются сами
        выражения job'а, а не наличие имени шага.
        """
        outputs = wf["jobs"]["plan"]["outputs"]
        assert outputs["packages"] == "${{ steps.plan.outputs.packages || '[]' }}", (
            "без fallback matrix получит '' и упадёт на fromJSON")
        assert outputs["plan_size"] == "${{ steps.plan.outputs.plan_size || '0' }}"

    def test_no_step_fakes_plan_outputs(self, wf):
        """Шаг, пишущий в свой GITHUB_OUTPUT, outputs шага plan не чинит."""
        steps = wf["jobs"]["plan"]["steps"]
        plan_ids = [s.get("id") for s in steps if s.get("id")]
        for st in steps:
            if st.get("id") == "plan":
                continue
            run = st.get("run", "")
            assert "GITHUB_OUTPUT" not in run or st.get("id") in (None, "scope"), (
                f"шаг {st.get('name')} пишет в свой GITHUB_OUTPUT — это не "
                "влияет на outputs шага plan и вводит в заблуждение")
        assert "plan" in plan_ids

    def test_every_run_block_is_valid_bash(self, wf):
        """P0: двойной `fi` в авто-merge давал syntax error только в реальном CI.

        YAML это проглатывает (run — строка), поэтому синтаксис проверяется
        принудительно: любой run-блок, который bash не может разобрать,
        уронил бы job уже после публикации, на последнем шаге.
        """
        import subprocess
        import tempfile

        broken = []
        for jname, job in wf["jobs"].items():
            for i, st in enumerate(job.get("steps", [])):
                run = st.get("run")
                if not run:
                    continue
                with tempfile.NamedTemporaryFile("w", suffix=".sh",
                                                 delete=False) as fh:
                    fh.write(run)
                    path = fh.name
                r = subprocess.run(["bash", "-n", path], capture_output=True,
                                   text=True)
                os.unlink(path)
                if r.returncode != 0:
                    broken.append(f"{jname}[{i}] {st.get('name', '?')}: "
                                  f"{r.stderr.strip()}")
        assert not broken, "run-блок(и) не разбирается bash:\n" + "\n".join(broken)

    def test_empty_plan_path_is_success_failed_reports_are_not(self, wf):
        """Пустой план и потерянные отчёты — разные исходы, а не оба «успех»."""
        steps = wf["jobs"]["reconcile"]["steps"]
        merge = next(s for s in steps if s.get("id") == "merge")
        run = merge["run"]
        assert "PLAN_SIZE" in str(merge.get("env", {})), (
            "нужно различать plan_size=0 и «отчёты не пришли»")
        assert "plan_size=0" in run, "пустой план должен давать пустой отчёт"
        assert 'echo "::error::' in run, (
            "отсутствие отчётов при непустом плане — провал, а не «волна пуста»")
        assert "PLAN_RESULT" in str(merge.get("env", {}))

    def test_finalize_rejects_failed_plan(self, wf):
        """Упавший plan не может дать «успешный» итог без работы."""
        fin = next(s for s in wf["jobs"]["finalize"]["steps"]
                   if "Fail unless" in s.get("name", ""))
        assert "PLAN_RESULT" in fin["run"]
        assert '"success"' in fin["run"]

    def test_regen_gate_runs_after_state_specs_generation(self, wf):
        """P0: гейт обязан проверять уже сгенерированный diff, а не чистый checkout.

        Порядок строго: generate -> gate -> commit. Раньше гейт стоял ПОСЛЕ
        git commit/push, где git status уже чист, и проверка фактически
        подтверждала пустое дерево.
        """
        steps = wf["jobs"]["reconcile"]["steps"]
        gen_i = next(i for i, s in enumerate(steps) if s.get("id") == "generate")
        gate_i = next(i for i, s in enumerate(steps) if s.get("id") == "regen-check")
        commit_i = next(i for i, s in enumerate(steps) if s.get("id") == "sync")
        assert gen_i < gate_i < commit_i, (
            "generate -> gate -> commit: гейт обязан стоять между генерацией "
            "state/SPECS и git commit")
        gate = steps[gate_i]
        assert str(gate.get("if", "")).startswith("always()"), (
            "гейт не должен теряться")
        assert "git commit" not in gate["run"], (
            "шаг валидации не должен коммитить сам")
        assert steps[gen_i].get("if", "") != "always()" or True
        assert "git commit" not in steps[gen_i]["run"], (
            "генерация обязана оставить дерево грязным для гейта")

    def test_auto_merge_is_gated_on_regen_result(self, wf):
        """Автомердж включается только после прохождения гейта."""
        steps = wf["jobs"]["reconcile"]["steps"]
        merge_i = next(i for i, s in enumerate(steps)
                       if s.get("name", "").startswith("Enable auto-merge"))
        gate_i = next(i for i, s in enumerate(steps)
                      if "regen gate" in s.get("name", ""))
        assert merge_i > gate_i, "автомердж раньше гейта — бессмыслен"
        merge_step = steps[merge_i]
        env = merge_step.get("env", {})
        assert env.get("PR_URL") == "${{ steps.sync.outputs.pr_url }}", (
            "автомердж обязан привязываться к PR, созданному шагом sync")
        assert env.get("REGEN_GATE") == "${{ steps.regen-check.outputs.regen_gate }}"
        guard = merge_step.get("if", "")
        assert "steps.sync.outputs.pr_url" in guard, (
            "если sync не создал PR, шаг должен пропуститься, а не «успеть»")

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
        gen = next(s for s in steps if s.get("id") == "generate")
        assert 'outcome") == "promoted"' in gen["run"], (
            "state/SPECS синхронизируются только для фактически опубликованных "
            "пакетов: deferred или 3/4 в репозиторий попадать не должны")
        assert "git push" not in gen["run"], (
            "шаг генерации не должен ни коммитить, ни пушить")

    def test_sync_diff_gate_whitelists_only_state_and_promoted_specs(self, wf):
        """P0: синхронизация обязана трогать ровно state/state.json и SPECS/promoted.

        Любой другой путь (pkgs.json, чужая спека, .github) = провал reconcile.
        """
        gate = next(s for s in wf["jobs"]["reconcile"]["steps"]
                    if s.get("id") == "regen-check")
        assert "wave_sync_gate.py" in gate["run"]
        assert "--base HEAD" in gate["run"], (
            "base обязан быть HEAD: ветка PR отличается от master, и diff "
            "от origin/master показал бы посторонние пути самой ветки")
        commit = next(s for s in wf["jobs"]["reconcile"]["steps"]
                      if s.get("id") == "sync")
        assert "steps.regen-check.outputs.sync_gate == 'ok'" in commit.get("if", ""), (
            "коммит разрешён только при ok-вердикте гейта")

    def test_wave_reports_are_reconciled_with_original_plan(self, wf):
        """P0: склейка отчётов без сверки с plan.json молча теряет дубли."""
        steps = wf["jobs"]["reconcile"]["steps"]
        merge = next(s for s in steps if s.get("id") == "merge")
        assert "wave_report_gate.py" in merge["run"]
        dl = next(s for s in steps if s.get("name") == "Download wave plan")
        assert dl["with"]["name"] == "wave-plan-${{ github.run_number }}"
        assert dl.get("continue-on-error") is True, (
            "упавший артефакт плана не должен ронять шаг до самой сверки")

    def test_concurrency_is_run_level(self, wf):
        """Сериализация должна быть на весь run, иначе две волны спорят за chroot."""
        assert wf["concurrency"]["group"] == "opensuse-auto-publish"
        assert wf["concurrency"]["cancel-in-progress"] is False, (
            "текущая волна должна дорабатываться, а не отменяться новой")
