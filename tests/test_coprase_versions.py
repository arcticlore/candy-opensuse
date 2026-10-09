#!/usr/bin/env python3
"""test_coprase_versions.py — date-roll не двигает цель при неизменном commit.

Регрессия к багу: api_ver.sh для untagged-репозитория генерирует версию как
``<дата-UTC>.<короткий-SHA>``. Раньше ЛЮБАЯ дата рождала «новую» цель, даже
если SHA не менялся: ежедневный крон ставил в план пересборку неизменного
апстрима (ghfetch/pokemon-icat). Цель обязана меняться только при смене SHA
(или для обычных релизов — при смене версии).
"""
import json
import os

import pytest

from conftest import load_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def cs():
    return load_module("coprase_status",
                       os.path.join(ROOT, "bin/coprase-status.py"))


class TestSameCommitTarget:
    @pytest.mark.parametrize("old,new,same", [
        ("20261002.4b44a4f", "20261009.4b44a4f", True),   # тот же SHA, иная дата
        ("20261002.4b44a4f", "20261002.4b44a4f", True),   # идентичны
        ("20261002.54d4bc5", "20261009.54d4bc5", True),   # pokemon-icat
        ("20261002.4b44a4f", "20261009.deadbee", False),  # новый SHA
        ("1.0.0", "1.0.0", False),                        # не date.hash
        ("", "", False),
        ("20261002.4b44a4f", "1.0.0", False),
        ("20261002.4b44a4f", "20261002", False),          # без .hash
    ])
    def test_matrix(self, cs, old, new, same):
        assert cs.same_commit_target(old, new) is same

    def test_hash_case_insensitive(self, cs):
        assert cs.same_commit_target("20261002.4B44A4F", "20261009.4b44a4f")


class TestDecideVersionUpdate:
    def test_unchanged_commit_does_not_move_target(self, cs):
        changed, target = cs.decide_version_update(
            "20261002.4b44a4f", "20261009.4b44a4f")
        assert changed is False
        assert target == "20261002.4b44a4f", "дата не должна двигать цель"

    def test_same_value_is_noop(self, cs):
        assert cs.decide_version_update("1.2.3", "1.2.3") == (False, "1.2.3")

    def test_new_commit_moves_target(self, cs):
        changed, target = cs.decide_version_update(
            "20261002.4b44a4f", "20261009.deadbee")
        assert changed is True
        assert target == "20261009.deadbee"

    def test_new_release_moves_target(self, cs):
        assert cs.decide_version_update("3.7.0", "3.7.1") == (True, "3.7.1")


class TestCmdVersionsDateRoll:
    """Функционально: cmd_versions не двигает цель при том же commit-hash."""

    def _repo(self, cs, tmp_path, monkeypatch, upstream, state_ver):
        (tmp_path / "state").mkdir()
        (tmp_path / "pkgs.json").write_text(json.dumps({
            "packages": [{"name": "ghfetch", "enabled": True}]}))
        (tmp_path / "state/state.json").write_text(json.dumps(
            {"ghfetch": {"ver": state_ver, "ts": 1}}))
        monkeypatch.chdir(tmp_path)

        class _R:
            returncode = 0
            stdout = upstream + "\n"
            stderr = ""

        monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: _R())

    def test_same_sha_new_date_keeps_target(self, cs, tmp_path, monkeypatch):
        self._repo(cs, tmp_path, monkeypatch,
                   "20261009.4b44a4f", "20261002.4b44a4f")
        cs.cmd_versions()
        st = json.loads((tmp_path / "state/state.json").read_text())
        assert st["ghfetch"]["ver"] == "20261002.4b44a4f"
        assert st["ghfetch"]["ts"] == 1, "ts не обновляется без смены цели"

    def test_new_sha_moves_target(self, cs, tmp_path, monkeypatch):
        self._repo(cs, tmp_path, monkeypatch,
                   "20261009.deadbee", "20261002.4b44a4f")
        cs.cmd_versions()
        st = json.loads((tmp_path / "state/state.json").read_text())
        assert st["ghfetch"]["ver"] == "20261009.deadbee"
        assert st["ghfetch"]["ts"] != 1
