#!/usr/bin/env python3
"""test_retry_brake.py — cooldown и mirror-brake против build storm."""
import os

from conftest import ROOT, load_module

rb = load_module("retry_brake", os.path.join(ROOT, "bin/retry_brake.py"))


class TestCooldown:
    def test_first_failure_blocks_resubmit(self):
        data = rb.load("/nonexistent")
        rb.record_failure(data, "dysk", "3.7.1", now=1_000_000)
        ok, reason = rb.cooldown_ok(data, "dysk", "3.7.1", now=1_000_060,
                                    cooldown_min=1440)
        assert ok is False and "cooldown" in reason

    def test_cooldown_expiry_allows_retry(self):
        data = rb.load("/nonexistent")
        rb.record_failure(data, "dysk", "3.7.1", now=1_000_000)
        ok, _ = rb.cooldown_ok(data, "dysk", "3.7.1", now=1_000_000 + 1441 * 60,
                               cooldown_min=1440)
        assert ok is True

    def test_untouched_package_is_allowed(self):
        data = rb.load("/nonexistent")
        ok, _ = rb.cooldown_ok(data, "dua", "2.45.1", now=1_000_000)
        assert ok is True

    def test_success_clears_cooldown(self):
        data = rb.load("/nonexistent")
        rb.record_failure(data, "dysk", "3.7.1", now=1)
        rb.record_success(data, "dysk", "3.7.1", now=2)
        ok, _ = rb.cooldown_ok(data, "dysk", "3.7.1", now=3, cooldown_min=1440)
        assert ok is True

    def test_cooldown_is_per_target(self):
        data = rb.load("/nonexistent")
        rb.record_failure(data, "dysk", "3.7.1", now=1_000_000)
        ok, _ = rb.cooldown_ok(data, "dysk", "3.7.2", now=1_000_060)
        assert ok is True

    def test_fail_count_accumulates(self):
        data = rb.load("/nonexistent")
        rb.record_failure(data, "dysk", "3.7.1", now=1)
        rb.record_failure(data, "dysk", "3.7.1", now=2)
        assert data["packages"]["dysk@3.7.1"]["fail_count"] == 2


class TestMirrorBrake:
    def test_same_signature_arms_after_threshold(self):
        data = rb.load("/nonexistent")
        rb.observe_mirror(data, "sig", ok=False, threshold=2)
        assert data["armed"] is False
        rb.observe_mirror(data, "sig", ok=False, threshold=2)
        assert data["armed"] is True

    def test_success_resets_signature(self):
        data = rb.load("/nonexistent")
        rb.observe_mirror(data, "sig", ok=False, threshold=2)
        rb.observe_mirror(data, "sig", ok=True, threshold=2)
        assert "sig" not in data["mirror"]
        assert data["armed"] is False

    def test_different_signatures_do_not_arm(self):
        data = rb.load("/nonexistent")
        rb.observe_mirror(data, "sig1", ok=False, threshold=2)
        rb.observe_mirror(data, "sig2", ok=False, threshold=2)
        assert data["armed"] is False

    def test_armed_blocks_submit_even_fresh_package(self):
        data = rb.load("/nonexistent")
        rb.observe_mirror(data, "sig", ok=False, threshold=1)
        ok, reason = rb.submit_allowed(data, "dua", "2.45.1", now=1)
        assert ok is False and "brake" in reason

    def test_reset_disarms(self):
        data = rb.load("/nonexistent")
        rb.observe_mirror(data, "sig", ok=False, threshold=1)
        rb.reset(data)
        ok, _ = rb.submit_allowed(data, "dua", "2.45.1", now=1)
        assert ok is True


class TestPersistenceAndCli:
    def test_save_load_roundtrip(self, tmp_path):
        path = tmp_path / "brake.json"
        data = rb.load(str(path))
        rb.record_failure(data, "dysk", "3.7.1", now=5)
        rb.save(str(path), data)
        again = rb.load(str(path))
        assert again["packages"]["dysk@3.7.1"]["last_fail_ts"] == 5

    def test_corrupt_file_is_safe_default(self, tmp_path):
        path = tmp_path / "brake.json"
        path.write_text("{not json")
        data = rb.load(str(path))
        assert data["armed"] is False

    def test_cli_check_exit_codes(self, tmp_path, capsys):
        path = str(tmp_path / "brake.json")
        assert rb.main(["--file", path, "check", "--name", "dysk",
                        "--target", "3.7.1"]) == 0
        rb.main(["--file", path, "record-failure", "--name", "dysk",
                 "--target", "3.7.1"])
        assert rb.main(["--file", path, "check", "--name", "dysk",
                        "--target", "3.7.1"]) == 1

    def test_cli_observe_mirror_arms(self, tmp_path):
        path = str(tmp_path / "brake.json")
        rb.main(["--file", path, "--threshold", "1", "observe-mirror",
                 "--fingerprint", "sig", "--ok", "false"])
        assert rb.load(path)["armed"] is True
