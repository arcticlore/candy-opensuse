#!/usr/bin/env python3
"""test_gen_specs.py — тесты генератора RPM-спеков."""
import os, sys, json, pytest, tempfile, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

class TestGenSpecs:
    """Тесты для gen_specs.py"""

    def test_import(self):
        """Модуль импортируется без ошибок"""
        import gen_specs
        assert hasattr(gen_specs, 'main') or hasattr(gen_specs, 'generate')

    def test_ecosystem_coverage(self, pkgs_json):
        """Все экосистемы в pkgs.json поддерживаются генератором"""
        supported_ecos = {"cargo", "go", "npm", "gem", "nim", "zig", "prebuilt",
                         "python-pkg", "python-script", "script",
                         "c-custom", "c-make", "c-cmake", "c-autotools", "meson", "custom"}
        actual_ecos = set(p.get("eco", "") for p in pkgs_json["packages"])
        unsupported = actual_ecos - supported_ecos
        assert not unsupported, f"Неподдерживаемые экосистемы: {unsupported}"

    def test_all_packages_have_required_fields(self, pkgs_json):
        """Все пакеты имеют обязательные поля"""
        required = {"name", "eco"}
        for pkg in pkgs_json["packages"]:
            if not pkg.get("enabled", True):
                continue
            missing = required - set(pkg.keys())
            assert not missing, f"Пакет {pkg.get('name')}: нет полей {missing}"

    def test_no_duplicate_names(self, pkgs_json):
        """Нет дублирующихся имён пакетов"""
        names = [p["name"] for p in pkgs_json["packages"]]
        dupes = [n for n in names if names.count(n) > 1]
        assert not dupes, f"Дублирующиеся имена: {set(dupes)}"

    def test_versions_are_strings(self, pkgs_json):
        """Версии — строки (не числа)"""
        for pkg in pkgs_json["packages"]:
            if "ver" in pkg:
                assert isinstance(pkg["ver"], str), \
                    f"Пакет {pkg['name']}: ver={pkg['ver']!r} должен быть строкой"

    def test_br_is_list(self, pkgs_json):
        """BuildRequires — список"""
        for pkg in pkgs_json["packages"]:
            if "br" in pkg:
                assert isinstance(pkg["br"], list), \
                    f"Пакет {pkg['name']}: br должен быть списком"

    def test_no_empty_names(self, pkgs_json):
        """Нет пакетов с пустым именем"""
        for pkg in pkgs_json["packages"]:
            assert pkg.get("name"), "Пакет с пустым именем"

    def test_chroots_not_empty(self, pkgs_json):
        """Список чрутов не пуст"""
        chroots = pkgs_json.get("project", {}).get("chroots", [])
        assert len(chroots) > 0, "Нет чрутов в конфигурации"

    def test_copr_name_format(self, pkgs_json):
        """Имя COPR репо в правильном формате"""
        name = pkgs_json.get("project", {}).get("copr_name", "")
        assert "/" in name, f"COPR имя должно содержать '/': {name}"
        owner, project = name.split("/", 1)
        assert owner and project, f"Неполное COPR имя: {name}"

    def test_cargo_body_no_fedora_macros(self, sample_pkg):
        """openSUSE: в cargo-спеке не должно быть Fedora-макросов %cargo_*"""
        import gen_specs
        m = gen_specs.Package(**sample_pkg)
        body = gen_specs.body_cargo(m, list(sample_pkg.get("br", [])), [])
        for macro in ("%cargo_prep", "%cargo_build", "%cargo_install", "cargo-rpm-macros"):
            assert macro not in body, f"Скрыт Fedora-макрос {macro} в cargo-бэкенде"
        assert "cargo build --release --offline" in body, "нет offline-сборки"
        assert "replace-with = \"vendored-sources\"" in body, "нет vendored-sources"

    def test_cargo_body_installs_binary(self, sample_pkg):
        """cargo-бэкенд ставит бинарники через install -Dpm0755"""
        import gen_specs
        m = gen_specs.Package(**sample_pkg)
        body = gen_specs.body_cargo(m, [], [])
        assert "install -Dpm0755 target/release/" in body, "нет установки из target/release"
        assert f"%{{_bindir}}/{sample_pkg['bins'][0]}" in body, \
            f"нет %{{_bindir}}/{sample_pkg['bins'][0]} в %files"

    def test_prebuilt_body_no_build_no_network(self, pkgs_json):
        """prebuilt-бэкенд: без zig/сборки/BR, ассеты по %{_arch}, оба SOURCE в files"""
        import gen_specs
        p = {"name": "linuxwave", "eco": "prebuilt", "host": "github",
             "slug": "orhun/linuxwave", "ver": "0.4.0", "tagp": "v",
             "topdir": "%{name}-%{version}", "bins": ["linuxwave"],
             "assets": {"x86_64": "linuxwave-%{version}-x86_64-linux.tar.gz",
                        "aarch64": "linuxwave-%{version}-aarch64-linux.tar.gz"},
             "man1": True}
        m = gen_specs.Package(**p)
        body = gen_specs.body_prebuilt(m, [], [])
        assert "zig " not in body and "BuildRequires" not in body
        assert "case %{_arch} in" in body
        assert 'x86_64) T="%{SOURCE0}";;' in body
        assert 'aarch64) T="%{SOURCE1}";;' in body
        assert "tar -xzf" in body
        assert "%{_bindir}/linuxwave" in body
        assert "%{_mandir}/man1/%{name}.1*" in body

    def test_prebuilt_header_two_sources_only(self, pkgs_json):
        """prebuilt-спека: Source0/1 = оба ассета, никакого source-тарбола"""
        import gen_specs
        p = {"name": "linuxwave", "eco": "prebuilt", "host": "github",
             "slug": "orhun/linuxwave", "ver": "0.4.0", "tagp": "v",
             "topdir": "%{name}-%{version}", "bins": ["linuxwave"],
             "assets": {"x86_64": "linuxwave-%{version}-x86_64-linux.tar.gz",
                        "aarch64": "linuxwave-%{version}-aarch64-linux.tar.gz"},
             "man1": True}
        m = gen_specs.Package(**p)
        spec = gen_specs.render("linuxwave", "0.4.0", {"linuxwave": m})
        assert "Source0:        linuxwave-%{version}-x86_64-linux.tar.gz" in spec
        assert "Source1:        linuxwave-%{version}-aarch64-linux.tar.gz" in spec
        assert "Source0:        %{name}-%{version}.tar.gz" not in spec
        assert "BuildRequires" not in spec
        assert "zig" not in spec

    def test_suse_name_map(self):
        """Fedora-имена BR переводятся в openSUSE (Tumbleweed: python3 => 3.13)"""
        import gen_specs
        cases = {
            "cargo-rpm-macros": "cargo-packaging",
            "python3-dbus": "python313-dbus-python",
            "python3-distro": "python313-distro",
            "python3-netifaces": "python313-netifaces",
            "python3-setproctitle": "python313-setproctitle",
            "python3-colorama": "python313-colorama",
            "python3-rich": "python313-rich",
            "libusb1-devel": "libusb-1_0-devel",
            "libjpeg-turbo-devel": "libjpeg8-devel",
            "glslang": "glslang-devel",
            "python3-devel": "python313-devel",
            "python3": "python313",
            "golang": "go",
        }
        for fedora, suse_name in cases.items():
            assert gen_specs.suse(fedora) == suse_name, \
                f"suse({fedora!r}) = {gen_specs.suse(fedora)!r}, ожидалось {suse_name!r}"

    def test_suse_name_map_contains_legacy(self):
        """Не должно остаться Fedora-имён в pkgs.json, непереведённых сuse()"""
        import json, os, gen_specs
        pkgs = json.load(open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "pkgs.json")))
        fedora_only = {"python3-dbus", "libjpeg-turbo-devel", "libusb1-devel"}
        for pkg in pkgs["packages"]:
            for br in pkg.get("br", []):
                base = br.split()[0] if br.split() else br
                assert base not in fedora_only or gen_specs.suse(base) != base, \
                    f"{pkg['name']}: BR {base!r} не переведён для openSUSE"

    def test_licence_british_spelling_packaged(self):
        """license_file="LICENCE" (UK) упаковывается: glob LICEN[CS]E* + %license-запись"""
        import gen_specs
        p = {"name": "tailspin", "eco": "cargo", "host": "github",
             "slug": "bensadeh/tailspin", "ver": "7.0.0", "tagp": "",
             "topdir": "tailspin-7.0.0", "bins": ["tspin"],
             "license_file": "LICENCE"}
        m = gen_specs.Package(**p)
        spec = gen_specs.render("tailspin", "7.0.0", {"tailspin": m})
        assert "for f in LICEN[CS]E* COPYING*" in spec, \
            "loop не матчил файл LICENCE (UK-написание)"
        assert "%license %{_licensedir}/%{name}/LICENCE" in spec, \
            "нет %license-записи %{_licensedir}/%{name}/LICENCE"
        assert "target/release/tspin" in spec, "нет установки target/release/tspin"

    def test_topdir_persisted(self, pkgs_json):
        """Все enabled-пакеты имеют topdir (идемпотентность --all)"""
        missing = [p["name"] for p in pkgs_json["packages"]
                   if p.get("enabled", True) and not p.get("topdir")]
        assert not missing, f"Нет topdir у: {missing}"

    def test_license_file_cargo_single(self, sample_pkg):
        """license_file на cargo-пакете даёт явный %license без дублей/каталога"""
        import gen_specs
        m = gen_specs.Package(**{**sample_pkg, "license_file": "LICENSE"})
        spec = gen_specs.render(m.name, m.ver or "1.0.0", {m.name: m})
        lines = spec.splitlines()
        files_block = lines[lines.index("%files"):lines.index("%changelog")]
        assert "%license %{_licensedir}/%{name}/LICENSE" in files_block
        assert "%{_licensedir}/%{name}" not in [
            ln for ln in files_block if not ln.startswith("%license ")], \
            "перекрывающийся каталог не должен оставаться в %files"
        assert files_block.count("%license %{_licensedir}/%{name}/LICENSE") == 1

    def test_license_files_array(self, sample_pkg):
        """license_files = [MIT, Apache]: обе записи %license, порядок сохранён"""
        import gen_specs
        m = gen_specs.Package(**{**sample_pkg,
                                 "license_files": ["LICENSE-MIT", "LICENSE-APACHE"]})
        spec = gen_specs.render(m.name, "1.0.0", {m.name: m})
        lines = spec.splitlines()
        files_block = lines[lines.index("%files"):lines.index("%changelog")]
        assert ("%license %{_licensedir}/%{name}/LICENSE-MIT\n"
                "%license %{_licensedir}/%{name}/LICENSE-APACHE") == \
            "\n".join(l for l in files_block if l.startswith("%license %{_licensedir}"))
        assert "%{_licensedir}/%{name}" not in [
            ln for ln in files_block if ln.startswith("%{_licensedir}")], \
            "не должно оставаться перекрывающихся записей каталога"
        assert sum(l.startswith("%license") for l in files_block) == 2
        # body_cargo остаётся без Fedora-макросов
        assert "cargo build --release --offline" in spec

    def test_out_flag_writes_valid_spec_atomically(self, pkgs_json, tmp_path,
                                                   monkeypatch, capsys):
        """Bug A: reconcile писал spec в stdout, а не в SPECS/<name>.spec.

        state обновлялся, спека оставалась прежней -> regen_gate валился на
        idempotency. CLI обязан писать файл, а spec — не утекать в stdout.
        """
        import gen_specs, re
        pkg = next(p for p in pkgs_json["packages"] if p["name"] == "duf")
        out = tmp_path / "SPECS" / "duf.spec"
        monkeypatch.setattr(sys, "argv",
                            ["gen_specs.py", "duf", "9.9.9", "--out", str(out)])
        gen_specs.main()
        captured = capsys.readouterr()
        spec = out.read_text()
        assert re.search(r"^Name:\s+duf\s*$", spec, re.MULTILINE)
        assert re.search(r"^Version:\s+9\.9\.9\s*$", spec, re.MULTILINE)
        assert "%changelog" in spec
        assert captured.out.strip() == "", "spec не должен утекать в stdout"
        assert not (out.with_name(out.name + ".tmp")).exists(), "остался .tmp"

    def test_out_matches_render(self, tmp_path):
        """--out пишет ровно то, что отдал бы render()."""
        import gen_specs
        from pathlib import Path
        meta = gen_specs.make_meta(
            gen_specs.load_pkgs(Path(ROOT) / "pkgs.json"))
        out = tmp_path / "duf.spec"
        gen_specs.write_spec_atomic("duf", "1.2.3", meta, out)
        assert out.read_text() == gen_specs.render("duf", "1.2.3", meta)

    def test_validate_rejects_bad_specs(self):
        import gen_specs
        for bad in ("", "   \n", "garbage\n", "Name: duf\nVersion: 1.0\n",
                    "Name: other\nVersion: 1.0\n%changelog\n"):
            with pytest.raises(ValueError):
                gen_specs.validate_spec(bad, "duf", "1.0")

    def test_atomic_write_keeps_old_file_on_invalid_render(self, tmp_path,
                                                           monkeypatch):
        """Провал генерации НЕ перезаписывает боевую спеку и не оставляет .tmp."""
        import gen_specs
        from pathlib import Path
        meta = gen_specs.make_meta(
            gen_specs.load_pkgs(Path(ROOT) / "pkgs.json"))
        out = tmp_path / "duf.spec"
        out.write_text("OLD-SPEC\n")
        monkeypatch.setattr(gen_specs, "render", lambda *a, **k: "")
        with pytest.raises(ValueError):
            gen_specs.write_spec_atomic("duf", "1.2.3", meta, out)
        assert out.read_text() == "OLD-SPEC\n"
        assert not (out.with_name(out.name + ".tmp")).exists()

    def test_license_files_autoextract_to_files(self, sample_pkg):
        """Тело без %license (c-make) получает список записей после %files"""
        import gen_specs
        p = {**{k: v for k, v in sample_pkg.items()}, "eco": "c-make",
             "bins": [], "install_cmd": "install -Dpm0755 foo %{buildroot}%{_bindir}/foo"}
        p["bins"] = ["foo"]
        m = gen_specs.Package(**{**p, "license_files": ["LICENSE", "COPYING"]})
        spec = gen_specs.render(m.name, "1.0.0", {m.name: m})
        lines = spec.splitlines()
        fi = lines.index("%files")
        lic = [ln for ln in lines[fi:] if ln.startswith("%license ")]
        assert len(lic) == 2
        assert lic[0] == "%license %{_licensedir}/%{name}/LICENSE"
