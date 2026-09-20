"""test_phase_d_inventory.py — unit tests for Phase D inventory logic."""
import pytest

from conftest import load_module

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PHASE_D = os.path.join(ROOT, "bin/phase-d-inventory.py")


@pytest.fixture
def mod():
    return load_module("phase_d_inventory", PHASE_D)


class TestClassifyLatest:
    def test_never_built(self, mod):
        assert mod.classify_latest(None, {}) == {
            c: "never-built" for c in mod.CHROOTS
        }

    def test_all_chroots_present_succeeded(self, mod):
        states = {c: "succeeded" for c in mod.CHROOTS}
        assert mod.classify_latest({"id": 1}, states) == states

    def test_partial_chroots_none_for_missing(self, mod):
        states = {mod.CHROOTS[0]: "succeeded"}  # билд только одного chroot
        out = mod.classify_latest({"id": 1}, states)
        assert out[mod.CHROOTS[0]] == "succeeded"
        for other in mod.CHROOTS[1:]:
            assert out[other] == "none"

    def test_failed_state_preserved(self, mod):
        states = {c: "succeeded" for c in mod.CHROOTS[:-1]}
        states[mod.CHROOTS[-1]] = "failed"
        out = mod.classify_latest({"id": 1}, states)
        assert out[mod.CHROOTS[-1]] == "failed"


class TestCategorize:
    def test_4_of_4_succeeded(self, mod):
        v = {c: "succeeded" for c in mod.CHROOTS}
        assert mod.categorize(v) == "succeeded"

    def test_one_failed(self, mod):
        v = {c: "succeeded" for c in mod.CHROOTS[:-1]}
        v[mod.CHROOTS[-1]] = "failed"
        assert mod.categorize(v) == "failed"

    def test_running_is_in_progress(self, mod):
        v = {c: "running" for c in mod.CHROOTS}
        assert mod.categorize(v) == "in-progress"

    def test_never_built(self, mod):
        v = {c: "never-built" for c in mod.CHROOTS}
        assert mod.categorize(v) == "never-built"

    def test_none_and_skipped_only(self, mod):
        v = dict.fromkeys(mod.CHROOTS, "none")
        v[mod.CHROOTS[0]] = "skipped"
        assert mod.categorize(v) == "edge-only"

    def test_canceled_only(self, mod):
        v = dict.fromkeys(mod.CHROOTS, "canceled")
        assert mod.categorize(v) == "edge-only"


class TestFingerprint:
    def test_spec_sha256_of_committed(self, mod):
        pkg = {"name": "archey4", "eco": "python", "host": "github",
               "slug": "HorlogeSkynet/archey4"}
        fp = mod.package_fingerprint(pkg, "4.15.0.0")
        assert fp["eco"] == "python"
        assert fp["host"] == "github"
        assert fp["slug"] == "HorlogeSkynet/archey4"
        assert fp["target_version"] == "4.15.0.0"
        assert len(fp["spec_sha256"]) == 16

    def test_missing_spec_reported(self, mod):
        pkg = {"name": "definitely-not-a-real-pkg-xyzzy", "eco": "c",
               "host": "github", "slug": "x/y"}
        fp = mod.package_fingerprint(pkg, "1.0")
        assert fp["spec_sha256"] == "no-spec-in-repo"


class TestRootCause:
    def test_no_match_for_argument(self, mod):
        text = (
            "error: some error\n"
            "No match for argument: go-toolset\n"
            "[1/1] builddep failed\n"
        )
        assert mod.classify_builder_log(text) == "builddep-unresolved"
        assert mod.missing_buildrequires(text) == ["go-toolset"]

    def test_failed_to_resolve_transaction(self, mod):
        text = "Failed to resolve the transaction: Nothing provides libfoo"
        assert mod.classify_builder_log(text) == "builddep-unresolved"

    def test_download_failure(self, mod):
        text = "Cannot download /path/tarball.gz: Connection refused"
        assert mod.classify_builder_log(text) == "source-download"

    def test_build_phase_error(self, mod):
        text = ("Compiling...\ngcc: error: unrecognized argument\n"
                "RPM build errors:")
        assert mod.classify_builder_log(text) == "build-phase-error"

    def test_empty_log_is_missing(self, mod):
        assert mod.classify_builder_log("") == "log-missing"

    def test_other_unrecognized(self, mod):
        assert mod.classify_builder_log("weird opaque output") == "other"

    def test_builddep_not_error_is_other(self, mod):
        # «builddep» упоминается, но без error-окружения — не таймаут
        text = "Running dnf5 builddep with resolved packages"
        assert mod.classify_builder_log(text) == "other"

    def test_builddep_timeout_with_errors(self, mod):
        text = ("dnf5 builddep\n\n"
                "Debug: connection timeout\n"
                "Error: failed to download")
        assert mod.classify_builder_log(text) == "builddep-timeout"