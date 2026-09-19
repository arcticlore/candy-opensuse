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
        supported_ecos = {"cargo", "go", "npm", "gem", "nim", "zig",
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

    def test_topdir_persisted(self, pkgs_json):
        """Все enabled-пакеты имеют topdir (идемпотентность --all)"""
        missing = [p["name"] for p in pkgs_json["packages"]
                   if p.get("enabled", True) and not p.get("topdir")]
        assert not missing, f"Нет topdir у: {missing}"
