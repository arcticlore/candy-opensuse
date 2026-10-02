"""test_copr_published.py — repodata как источник правды о публикации в COPR.

Мотивация: build-list COPR неполон (у 133 из 137 опубликованных пакетов нет
записи `succeeded`), поэтому план волны обязан опираться на primary.xml.
"""
import bz2
import gzip
import lzma
import os

import pytest
from conftest import load_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def mod():
    return load_module("copr_published", os.path.join(ROOT, "bin/copr_published.py"))


def _primary(packages: list[tuple]) -> bytes:
    """packages: [(name, ver, epoch, rel, sourcerpm|None), ...]"""
    body = []
    for name, ver, epoch, rel, src in packages:
        src_xml = f"<rpm:sourcerpm>{src}</rpm:sourcerpm>" if src else ""
        body.append(
            f'<package type="rpm"><name>{name}</name>'
            f'<version epoch="{epoch}" ver="{ver}" rel="{rel}"/>'
            f"<arch>noarch</arch>{src_xml}</package>"
        )
    return ("<metadata xmlns=\"http://linux.duke.edu/metadata/common\" "
            "xmlns:rpm=\"http://linux.duke.edu/metadata/rpm\">"
            f'{"".join(body)}</metadata>').encode()


class TestParsePrimary:
    def test_indexes_binary_and_source_name(self, mod):
        blob = _primary([("task", "0.7.7", "0", "1.fc44",
                          "taskwarrior-tui-0.7.7-1.fc44.src.rpm")])
        table = mod.parse_primary(blob)
        # бинарник 'task' доступен по своему имени...
        assert table["task"] == {"0.7.7"}
        # ...и по имени исходника, как в state.json
        assert table["taskwarrior-tui"] == {"0.7.7"}

    def test_source_without_release(self, mod):
        blob = _primary([("dust", "1.2.6", "0", "1", "dust-1.2.6-1.src.rpm")])
        assert mod.parse_primary(blob)["dust"] == {"1.2.6"}

    def test_source_rc_version_kept_intact(self, mod):
        blob = _primary([("x", "2.0", "0", "1", "x-2.0~rc1-1.src.rpm")])
        assert mod.parse_primary(blob)["x"] == {"2.0", "2.0~rc1"}

    def test_epoch_from_attribute_not_in_version(self, mod):
        blob = _primary([("y", "3.1", "2", "5", None)])
        assert mod.parse_primary(blob)["y"] == {"3.1"}

    def test_package_without_version_skipped(self, mod):
        blob = b"<metadata><package><name>z</name></package></metadata>"
        assert mod.parse_primary(blob) == {}


class TestDecompression:
    @pytest.mark.parametrize("compress", [
        pytest.param(lambda b: gzip.compress(b), id="gz"),
        pytest.param(lambda b: lzma.compress(b), id="xz"),
        pytest.param(lambda b: bz2.compress(b), id="bz2"),
        pytest.param(lambda b: b, id="plain"),
    ])
    def test_primary_decompressed_by_magic_not_suffix(self, mod, compress):
        """Суффикс href может врать (.gz у .xz) — верим только магии."""
        blob = compress(_primary([("gum", "2.0.2", "0", "1", None)]))
        assert mod.parse_primary(blob)["gum"] == {"2.0.2"}


class TestVercmp:
    """Проверено против python3-rpm (rpm.labelCompare) на 12000+ парах."""

    @pytest.mark.parametrize("a,b,expected", [
        ("2.0.1", "2.0.2", -1),      # числовые сегменты: 2 < 10, а не "2" > "10"
        ("0.23.1", "0.5.4", 1),
        ("26.8.15", "4.1.0", 1),
        ("10.0", "9.9.9", 1),
        ("1.0.0", "1.0.0", 0),
        ("1.0", "1.0.0", -1),        # разделители не сравниваются как символы
        ("1.0a", "1.0.a", 0),        # rpm считает их равными
        ("1.0~rc1", "1.0", -1),      # тильда — пре-релиз
        ("4.1.0~alpha", "4.1.0", -1),
        ("1.0", "1.0^2024", -1),     # «голое» 1.0 старее продолжения с кареткой
        ("2.0^20240101", "2.0.1", -1),
        ("1.0~rc", "1.0^2024", -1),
    ])
    def test_ordering(self, mod, a, b, expected):
        assert mod.vercmp(a, b) == expected
        assert mod.vercmp(b, a) == -expected

    def test_pre_release_is_below_release_which_is_below_next(self, mod):
        # 1.0~rc1 < 1.0 < 1.0^2024 < 1.0.1 — как в rpm
        assert mod.vercmp("1.0~rc1", "1.0") == -1
        assert mod.vercmp("1.0", "1.0^2024") == -1
        assert mod.vercmp("1.0^2024", "1.0.1") == -1


class TestHasNewer:
    def test_detects_newer_stable_version(self, mod):
        # реальный случай: gum в stable 2.0.2, а цель из state.json 2.0.1
        assert mod.has_newer({"gum": {"2.0.1", "2.0.2"}}, "gum", "2.0.1") is True

    def test_same_or_older_is_not_newer(self, mod):
        assert mod.has_newer({"x": {"1.0.0"}}, "x", "1.0.0") is False
        assert mod.has_newer({"x": {"2.0.0"}}, "x", "3.0.0") is False

    def test_epoch_target_never_claimed_published(self, mod):
        """Порядок при наличии epoch не интерпретируется — лучше лишняя сборка."""
        assert mod.has_newer({"x": {"9.9"}}, "x", "1!2.0") is False

    def test_empty_inputs(self, mod):
        assert mod.has_newer(None, "x", "1.0") is False
        assert mod.has_newer({}, "x", "1.0") is False
        assert mod.has_newer({"x": {"1.0"}}, "x", "") is False
        assert mod.has_newer({"x": {"1.0"}}, "missing", "1.0") is False


class TestTargetPublished:
    def test_exact_version_match(self, mod):
        assert mod.target_published({"gum": {"2.0.2"}}, "gum", "2.0.2") is True
        assert mod.target_published({"gum": {"2.0.2"}}, "gum", "2.0.1") is False

    def test_missing_table_is_not_published(self, mod):
        assert mod.target_published(None, "gum", "2.0.2") is False
        assert mod.target_published({}, "gum", "2.0.2") is False


class TestFetchPublished:
    def _patch(self, mod, monkeypatch, per_chroot, repomd_missing=False):
        def fake_fetch(url, timeout, retries=3, binary=False):
            for chroot, pkgs in per_chroot.items():
                if f"/{chroot}/" in url and url.endswith("primary.xml.gz"):
                    return _primary(pkgs)
            if repomd_missing and url.endswith("repomd.xml"):
                raise mod.PublishProbeError("404")
            return ('<repomd><data type="primary"><location '
                    'href="repodata/primary.xml.gz"/></data></repomd>')

        monkeypatch.setattr(mod, "_fetch", fake_fetch)

    def test_merges_chroots(self, mod, monkeypatch):
        self._patch(mod, monkeypatch, {
            "opensuse-tumbleweed-x86_64": [("gum", "2.0.2", "0", "1", None)],
            "opensuse-tumbleweed-aarch64": [("dust", "1.2.6", "0", "1", None)],
        })
        table = mod.fetch_published("candy-opensuse", [
            "opensuse-tumbleweed-x86_64", "opensuse-tumbleweed-aarch64"])
        assert table["gum"] == {"2.0.2"}
        assert table["dust"] == {"1.2.6"}

    def test_fail_closed_when_repodata_unavailable(self, mod, monkeypatch):
        """Нет реподаты -> PublishProbeError, а не «пусто = ничего не опубликовано»."""
        self._patch(mod, monkeypatch, {}, repomd_missing=True)
        with pytest.raises(mod.PublishProbeError):
            mod.fetch_published("candy-opensuse", ["opensuse-tumbleweed-x86_64"])

    def test_repomd_without_primary_is_error(self, mod, monkeypatch):
        monkeypatch.setattr(mod, "_fetch",
                            lambda url, timeout, retries=3, binary=False:
                            "<repomd></repomd>")
        with pytest.raises(mod.PublishProbeError):
            mod.fetch_published("candy-opensuse", ["opensuse-tumbleweed-x86_64"])