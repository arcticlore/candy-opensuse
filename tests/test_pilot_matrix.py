#!/usr/bin/env python3
"""test_pilot_matrix.py — матрица пилотной сборки: параллелизм, fail-fast, строгие гейты.

Идея перенесена из закрытой PR #15 (отдельный workflow pilot-sweep), но без
beta-имён и без второго workflow: матрица живёт в существующем pilot-build.yml.
Инварианты: максимум 3 активных parent builds, падение одного пакета не
отменяет остальные, неизвестное/disabled-имя = FAILURE (не SKIP), пакет без
exact 4/4 не может быть промотирован.
"""
import json
import os
import re
import subprocess
import sys
import textwrap

import pytest

yaml = pytest.importorskip("yaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github/workflows/pilot-build.yml")


@pytest.fixture(scope="module")
def wf_text():
    with open(WF, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def wf(wf_text):
    return yaml.safe_load(wf_text)


@pytest.fixture(scope="module")
def resolve_code(wf_text):
    """Тело python-скрипта из шага resolve (используется как подпроцесс)."""
    m = re.search(
        r"python3 - \"\$PKGS\" \"\$RESUME_BUILD\" <<'PY' \| tee logs/resolve\.log\n"
        r"(.*?)\n          PY\n", wf_text, re.S)
    assert m, "resolve-скрипт не найден"
    # YAML-блок `run: |` снимает общий отступ в рантайме, а в файле он остаётся;
    # без dedent `python3 -c` получает IndentationError.
    return textwrap.dedent(m.group(1))


def run_resolve(code, pkgs, resume=""):
    return subprocess.run([sys.executable, "-c", code, pkgs, resume],
                          capture_output=True, text=True, cwd=ROOT, timeout=120)


def test_matrix_parallelism_and_fail_fast(wf):
    strategy = wf["jobs"]["build"]["strategy"]
    assert strategy["max-parallel"] == 3, "больше 3 активных parent builds нельзя"
    assert strategy["fail-fast"] is False, "один упавший пакет не должен отменять wave"
    assert "fromJSON(needs.resolve.outputs.matrix)" in str(strategy["matrix"])


def test_resolve_job_gates_matrix(wf):
    resolve = wf["jobs"]["resolve"]
    assert "matrix" in resolve["outputs"], "resolve обязан отдавать матрицу"
    assert wf["jobs"]["build"]["needs"] == "resolve"


def test_pilot_project_and_no_beta_naming(wf_text):
    assert "arcticlore/candy-opensuse-pilot" in wf_text
    assert "candy-opensuse-beta" not in wf_text, "beta-канал в pilot-конвейере запрещён"
    assert "candy-opensuse-beta" not in wf_text.replace("beta-канал", "")


def test_unknown_name_is_failure(resolve_code):
    r = run_resolve(resolve_code, "definitely-not-a-package")
    assert r.returncode != 0, "неизвестное имя обязано быть FAILURE"
    assert "[FAIL]" in (r.stdout + r.stderr)


def test_disabled_package_is_failure(resolve_code):
    meta = json.load(open(os.path.join(ROOT, "pkgs.json")))["packages"]
    disabled = [p["name"] for p in meta
                if p.get("enabled") is not None
                and str(p["enabled"]).lower() in ("false", "0", "no")]
    assert disabled, "в pkgs.json должны быть disabled-пакеты для проверки"
    r = run_resolve(resolve_code, disabled[0])
    assert r.returncode != 0, "disabled-пакет не должен уходить в pilot"
    assert "disabled" in (r.stdout + r.stderr)


def test_duplicate_rejected(resolve_code):
    r = run_resolve(resolve_code, "archey4,archey4")
    assert r.returncode != 0
    assert "дубликат" in (r.stdout + r.stderr)


def test_empty_list_rejected(resolve_code):
    assert run_resolve(resolve_code, "  ").returncode != 0


def test_resume_must_match_length(resolve_code):
    r = run_resolve(resolve_code, "archey4", "1,2")
    assert r.returncode != 0
    assert "resume_build" in (r.stdout + r.stderr)


def test_matrix_entries_shape(resolve_code):
    r = run_resolve(resolve_code, "archey4,colorls", "11062660,11062661")
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads([ln for ln in r.stdout.splitlines()
                       if ln.strip().startswith('{"include"')][-1])
    assert [e["name"] for e in data["include"]] == ["archey4", "colorls"]
    assert [e["resume"] for e in data["include"]] == ["11062660", "11062661"]


def test_evidence_requires_exact_4_of_4(wf_text):
    """Evidence обязан фиксировать per-chroot состояния и считать exact 4/4."""
    assert "exact_4_of_4" in wf_text
    assert "build-chroot/list" in wf_text, "состояния берутся из COPR API, не из лога"
    assert "НЕ exact 4/4" in wf_text
    ev = wf_text[wf_text.index("python3 - \"$PKG\" \"$BUILD_ID\" \"$rc\""):]
    assert "len(chroots) == 4" in ev and 'v == "succeeded"' in ev


def test_silent_skip_forbidden(wf_text):
    assert "молчаливый skip запрещён" in wf_text
    assert "[SKIP]" not in wf_text.replace("'[SKIP]'", "").replace('grep -q', '')