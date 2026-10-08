#!/usr/bin/env python3
"""test_preflight.py — fail-closed mirror preflight.

Проверяет РЕШЕНИЕ preflight по чистым частям (без сети/zypper): зелёный путь,
несогласованность metadata/checksum, ошибку zypper, отсутствие пакета, битый
digest и стабильность fingerprint'а сбойного зеркала.
"""
import gzip
import hashlib
import json
import os

import pytest

from conftest import ROOT, load_module

pf = load_module("preflight", os.path.join(ROOT, "bin/preflight.py"))


# ---------------------------------------------------------------------------
# Фикстуры metadata
# ---------------------------------------------------------------------------
def _repomd(primary_href="repodata/primary.xml.gz", checksum="deadbeef"):
    return f"""<?xml version="1.0"?>
<repomd xmlns="http://linux.duke.edu/metadata/repo">
  <data type="primary">
    <location href="{primary_href}"/>
    <checksum type="sha256">{checksum}</checksum>
  </data>
</repomd>""".encode()


def _primary(name, version, location, checksum):
    return f"""<?xml version="1.0"?>
<metadata xmlns="http://linux.duke.edu/metadata/common">
  <package type="rpm">
    <name>{name}</name>
    <version ver="{version}"/>
    <location href="{location}"/>
    <checksum type="sha256">{checksum}</checksum>
  </package>
</metadata>""".encode()


class FakeRunner:
    def __init__(self, rpm_nv=("xh", "0.23.0"), rpm_k_rc=0, refresh_rc=0):
        self.rpm_nv = rpm_nv
        self.rpm_k_rc = rpm_k_rc
        self.refresh_rc = refresh_rc
        self.calls = []

    def run(self, cmd, timeout=600):
        self.calls.append(cmd)
        joined = " ".join(cmd)
        if cmd[:2] == ["zypper", "-n"] and "refresh" in cmd:
            return self.refresh_rc, "refreshed\n", "" if self.refresh_rc == 0 else "boom\n"
        if cmd[:2] == ["zypper", "-n"] and "install" in cmd:
            return 0, "Downloading\n", ""
        if cmd[:1] == ["zypper"]:
            return 0, "added\n", ""
        if cmd[:2] == ["rpm", "-qp"]:
            return 0, f"{self.rpm_nv[0]}\n{self.rpm_nv[1]}\n", ""
        if cmd[:2] == ["rpm", "-K"]:
            return self.rpm_k_rc, "digests OK\n" if self.rpm_k_rc == 0 else "BAD\n", ""
        return 0, "", ""


def _cfg(**kw):
    base = {"repo_url": "https://example.invalid/repo", "alias": "candy",
            "chroot": "opensuse-tumbleweed-x86_64", "package": "xh",
            "version": None}
    base.update(kw)
    return base


def _green_env(tmp_path, monkeypatch, content=b"rpm-bytes", **runner_kw):
    """Собрать согласованные metadata+rpm и подменить поиск скачанного файла."""
    rpm = tmp_path / "xh-0.23.0.x86_64.rpm"
    rpm.write_bytes(content)
    sha = hashlib.sha256(content).hexdigest()
    primary = _primary("xh", "0.23.0", "Packages/x/xh-0.23.0.x86_64.rpm", sha)
    gz = gzip.compress(primary)
    metadata = {"repomd": _repomd(checksum=sha),
                "primary": gz}

    def fetcher(url):
        if url.endswith("repomd.xml"):
            return metadata["repomd"]
        return metadata["primary"]

    monkeypatch.setattr(pf, "_find_downloaded",
                        lambda runner, alias, name: str(rpm))
    return fetcher, FakeRunner(**runner_kw), sha


class TestPreflightDecision:
    def test_green_preflight(self, tmp_path, monkeypatch):
        fetcher, runner, sha = _green_env(tmp_path, monkeypatch)
        res = pf.probe(_cfg(), runner=runner, fetcher=fetcher, log_dir=tmp_path)
        assert res["ok"] is True, res["reasons"]
        assert res["evidence"]["content"]["sha256"] == sha
        assert any(s["step"] == "zypper-download" and s["ok"] for s in res["steps"])
        assert json.loads((tmp_path / "preflight.json").read_text())["ok"] is True

    def test_checksum_mismatch_blocks(self, tmp_path, monkeypatch):
        """content не совпал с metadata -> RED, несмотря на зелёный zypper."""
        fetcher, runner, _ = _green_env(tmp_path, monkeypatch)
        # портим identity: rpm-байты дадут другой sha, чем в primary
        (tmp_path / "xh-0.23.0.x86_64.rpm").write_bytes(b"tampered")
        res = pf.probe(_cfg(), runner=runner, fetcher=fetcher, log_dir=tmp_path)
        assert res["ok"] is False
        assert any(r.startswith("checksum:") for r in res["reasons"]), res["reasons"]

    def test_metadata_transport_failure_blocks(self, tmp_path, monkeypatch):
        def fetcher(url):
            raise pf.PreflightError("http: 503")
        runner = FakeRunner()
        res = pf.probe(_cfg(), runner=runner, fetcher=fetcher, log_dir=tmp_path)
        assert res["ok"] is False
        # сеть упала -> до zypper-submit не доходим
        assert not any("install" in " ".join(c) for c in runner.calls)

    def test_zypper_refresh_failure_blocks(self, tmp_path, monkeypatch):
        fetcher, _runner, _ = _green_env(tmp_path, monkeypatch)
        runner = FakeRunner(refresh_rc=1)
        res = pf.probe(_cfg(), runner=runner, fetcher=fetcher, log_dir=tmp_path)
        assert res["ok"] is False
        assert any(r.startswith("zypper:") for r in res["reasons"]), res["reasons"]
        assert not any("install" in " ".join(c) for c in runner.calls)

    def test_missing_package_blocks(self, tmp_path, monkeypatch):
        rpm = tmp_path / "x.rpm"
        rpm.write_bytes(b"x")
        sha = hashlib.sha256(b"x").hexdigest()
        primary = _primary("other", "1.0", "Packages/o/other.rpm", sha)
        gz = gzip.compress(primary)

        def fetcher(url):
            return _repomd(checksum=sha) if url.endswith("repomd.xml") else gz
        res = pf.probe(_cfg(), runner=FakeRunner(), fetcher=fetcher,
                       log_dir=tmp_path)
        assert res["ok"] is False
        assert any("отсутствует" in r for r in res["reasons"]), res["reasons"]

    def test_bad_rpm_digest_blocks(self, tmp_path, monkeypatch):
        fetcher, _runner, _ = _green_env(tmp_path, monkeypatch)
        res = pf.probe(_cfg(), runner=FakeRunner(rpm_k_rc=1), fetcher=fetcher,
                       log_dir=tmp_path)
        assert res["ok"] is False
        assert any(r.startswith("checksum:") for r in res["reasons"])

    def test_version_mismatch_blocks(self, tmp_path, monkeypatch):
        fetcher, _runner, _ = _green_env(tmp_path, monkeypatch)
        res = pf.probe(_cfg(version="9.9.9"), runner=FakeRunner(), fetcher=fetcher,
                       log_dir=tmp_path)
        # в primary нет версии 9.9.9 -> metadata-ошибка до скачивания
        assert res["ok"] is False

    def test_fingerprint_stable_for_same_mirror(self):
        a = pf.fingerprint(_repomd(checksum="aaa"), "aaa")
        b = pf.fingerprint(_repomd(checksum="aaa"), "aaa")
        c = pf.fingerprint(_repomd(checksum="bbb"), "bbb")
        assert a == b != c

    def test_classify_tags(self):
        assert pf.classify("checksum: ...") == "checksum"
        assert pf.classify("metadata: ...") == "metadata"
        assert pf.classify("zypper: refresh rc=1") == "zypper"
        assert pf.classify("http: 503") == "http"

    def test_main_writes_out_and_exit_code(self, tmp_path, monkeypatch):
        fetcher, runner, _ = _green_env(tmp_path, monkeypatch)
        monkeypatch.setattr(pf, "SubprocessRunner", lambda: runner)
        monkeypatch.setattr(pf, "urllib_fetch", fetcher)
        out = tmp_path / "preflight.json"
        rc = pf.main(["--repo-url", "https://example.invalid/repo",
                      "--package", "xh", "--out", str(out),
                      "--log-dir", str(tmp_path)])
        assert rc == 0
        assert json.loads(out.read_text())["ok"] is True

    def test_main_red_returns_one(self, tmp_path, monkeypatch):
        def fetcher(url):
            raise pf.PreflightError("http: down")
        monkeypatch.setattr(pf, "SubprocessRunner", lambda: FakeRunner())
        monkeypatch.setattr(pf, "urllib_fetch", fetcher)
        rc = pf.main(["--repo-url", "https://example.invalid/repo",
                      "--package", "xh", "--log-dir", str(tmp_path)])
        assert rc == 1


class TestCoprKeyAndArch:
    def test_copr_pubkey_url(self):
        assert pf.copr_pubkey_url(
            "https://download.copr.fedorainfracloud.org/results/o/p/"
            "opensuse-tumbleweed-x86_64"
        ) == "https://download.copr.fedorainfracloud.org/results/o/p/pubkey.gpg"
        assert pf.copr_pubkey_url("https://example.org/repo") is None

    def test_primary_skips_src_rpm(self):
        primary = (
            b'<?xml version="1.0"?>'
            b'<metadata xmlns="http://linux.duke.edu/metadata/common">'
            b'<package type="rpm"><name>sd</name><arch>src</arch>'
            b'<version ver="1.1.0"/>'
            b'<location href="Packages/s/sd-1.1.0-1.suse.tw.src.rpm"/>'
            b'<checksum type="sha256">AAAA</checksum></package>'
            b'<package type="rpm"><name>sd</name><arch>x86_64</arch>'
            b'<version ver="1.1.0"/>'
            b'<location href="Packages/s/sd-1.1.0-1.suse.tw.x86_64.rpm"/>'
            b'<checksum type="sha256">BBBB</checksum></package>'
            b'</metadata>')
        got = pf.parse_primary(primary, "sd")
        assert got["arch"] == "x86_64"
        assert got["checksum"] == "BBBB"
        assert got["location"].endswith(".x86_64.rpm")

    def test_copr_key_is_imported_before_refresh(self, tmp_path, monkeypatch):
        rpm = tmp_path / "sd-1.1.0.x86_64.rpm"
        rpm.write_bytes(b"bytes")
        sha = hashlib.sha256(b"bytes").hexdigest()
        primary = _primary("sd", "1.1.0", "Packages/s/sd.x86_64.rpm", sha)
        gz = gzip.compress(primary)
        calls = []

        def fetcher(url):
            if url.endswith("pubkey.gpg"):
                calls.append("pubkey")
                return b"PGP-KEY"
            return _repomd(checksum=sha) if url.endswith("repomd.xml") else gz

        monkeypatch.setattr(pf, "_find_downloaded", lambda *a: str(rpm))
        runner = FakeRunner(rpm_nv=("sd", "1.1.0"))
        res = pf.probe(
            _cfg(repo_url="https://download.copr.fedorainfracloud.org/results/"
                          "o/p/opensuse-tumbleweed-x86_64", package="sd"),
            runner=runner, fetcher=fetcher, log_dir=tmp_path)
        assert res["ok"] is True, res["reasons"]
        assert "pubkey" in calls
        assert any(s["step"] == "pubkey" and s["ok"] for s in res["steps"])
        imports = [c for c in runner.calls if c[:2] == ["rpm", "--import"]]
        assert imports, "ключ COPR обязан импортироваться"
        assert runner.calls.index(imports[0]) < next(
            i for i, c in enumerate(runner.calls) if "refresh" in c)
