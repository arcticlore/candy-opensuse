"""test_regen_gate.py — тесты гейта «точный реген-дифф» (scope + spec-set + idempotency)."""
import json
import os

import pytest

from conftest import load_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def gate():
    return load_module("regen_gate", os.path.join(ROOT, "bin/regen_gate.py"))


class TestScope:
    def test_expected_paths_allowed(self, gate):
        changed = ["SPECS/duf.spec", "SPECS/tailspin.spec",
                   "state/state.json", "pkgs.json"]
        assert gate.check_scope(changed) == []

    def test_code_and_docs_changes_rejected(self, gate):
        bad = gate.check_scope(["README.md", "bin/gen_specs.py",
                                ".github/workflows/update.yml"])
        assert bad == [".github/workflows/update.yml", "README.md", "bin/gen_specs.py"]

    def test_workflow_only_change_fails_gate(self, gate):
        """Раньше бот-PR с правкой workflow проходил как «чистый реген»."""
        assert gate.check_scope([".github/workflows/update.yml"]) != []

    def test_untracked_specs_are_scoped(self, gate):
        assert gate.check_scope(["SPECS/newpkg.spec"]) == []

    def test_similar_prefix_not_allowed(self, gate):
        assert gate.check_scope(["SPECS/../bin/x"]) != []


class TestSpecSet:
    def test_exact_match_ok(self, gate):
        assert gate.check_spec_set({"a", "b"}, {"a", "b"}) == []

    def test_stale_disabled_spec_detected(self, gate):
        assert gate.check_spec_set({"a", "zenith"}, {"a"}) == ["stale-spec:zenith"]

    def test_missing_spec_detected(self, gate):
        assert gate.check_spec_set({"a"}, {"a", "b"}) == ["missing-spec:b"]

    def test_both_directions(self, gate):
        got = gate.check_spec_set({"old", "a"}, {"a", "new"})
        assert got == ["stale-spec:old", "missing-spec:new"]


class TestEnabledNames:
    def test_reads_pkgs_json(self, gate, tmp_path):
        p = tmp_path / "pkgs.json"
        p.write_text(json.dumps({"packages": [
            {"name": "a"}, {"name": "b", "enabled": False},
            {"name": "c", "enabled": "false"}, {"name": "d", "enabled": "true"},
        ]}))
        assert gate.enabled_names(p) == {"a", "d"}


class TestTreeHash:
    def test_hash_changes_with_content(self, gate, tmp_path):
        d = tmp_path / "SPECS"
        d.mkdir()
        (d / "a.spec").write_text("x")
        h1 = gate.tree_hash(d)
        (d / "a.spec").write_text("y")
        assert gate.tree_hash(d) != h1

    def test_hash_covers_filenames(self, gate, tmp_path):
        d = tmp_path / "SPECS"
        d.mkdir()
        (d / "a.spec").write_text("x")
        h1 = gate.tree_hash(d)
        (d / "a.spec").unlink()
        (d / "b.spec").write_text("x")
        assert gate.tree_hash(d) != h1

    def test_idempotent_when_nothing_changes(self, gate, tmp_path):
        d = tmp_path / "SPECS"
        d.mkdir()
        (d / "a.spec").write_text("x")
        assert gate.tree_hash(d) == gate.tree_hash(d)


class TestRealRepoGate:
    """Гейт на реальном дереве репозитория (лёгкий, без сборок)."""

    def test_code_change_fails_gate(self, gate, monkeypatch):
        """Правки bin/ или .github/ не должны проходить как «чистый реген»."""
        monkeypatch.setattr(gate, "git_changed_paths",
                            lambda root: ["bin/gen_specs.py"])
        assert gate.main([ROOT]) == 1

    def test_scoped_change_passes_gate(self, gate, monkeypatch):
        """Независимо от чистоты дерева чекаута: только SPECS/state/pkgs."""
        monkeypatch.setattr(gate, "git_changed_paths",
                            lambda root: ["SPECS/duf.spec", "state/state.json",
                                          "pkgs.json"])
        assert gate.main([ROOT]) == 0

    def test_workflow_change_fails_gate(self, gate, monkeypatch):
        monkeypatch.setattr(gate, "git_changed_paths",
                            lambda root: [".github/workflows/update.yml"])
        assert gate.main([ROOT]) == 1

    def test_repo_specs_match_enabled(self, gate):
        enabled = gate.enabled_names(os.path.join(ROOT, "pkgs.json"))
        assert gate.check_spec_set(gate.specs_present(os.path.join(ROOT, "SPECS")),
                                   enabled) == []

    def test_no_stale_disabled_specs_in_repo(self, gate):
        enabled = gate.enabled_names(os.path.join(ROOT, "pkgs.json"))
        assert "zenith" not in gate.specs_present(os.path.join(ROOT, "SPECS"))


class TestGitChangedPaths:
    def test_parses_porcelain_keeping_leading_space_paths(self, gate, tmp_path):
        """Регресс-проверка: strip() съедал первый символ пути."""
        import subprocess as sp
        from unittest import mock
        out = " M bin/auto-publish.py\n?? newfile.txt\nA  SPECS/a.spec\n"
        fake = mock.Mock(return_value=mock.Mock(returncode=0, stdout=out, stderr=""))
        with mock.patch.object(gate.subprocess, "run", fake):
            paths = gate.git_changed_paths(tmp_path)
        assert paths == ["bin/auto-publish.py", "newfile.txt", "SPECS/a.spec"]

    def test_rename_uses_new_path(self, gate, tmp_path):
        from unittest import mock
        out = "R  old/name.spec -> SPECS/name.spec\n"
        fake = mock.Mock(return_value=mock.Mock(returncode=0, stdout=out, stderr=""))
        with mock.patch.object(gate.subprocess, "run", fake):
            assert gate.git_changed_paths(tmp_path) == ["SPECS/name.spec"]

    def test_none_when_git_fails(self, gate, tmp_path):
        from unittest import mock
        fake = mock.Mock(return_value=mock.Mock(returncode=128, stdout="", stderr="not a repo"))
        with mock.patch.object(gate.subprocess, "run", fake):
            assert gate.git_changed_paths(tmp_path) is None