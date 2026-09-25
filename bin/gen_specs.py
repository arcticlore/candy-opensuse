#!/usr/bin/env python3
"""RPM spec generator from pkgs.json.

Usage:
    gen_specs.py NAME VERSION     # output spec for one package
    gen_specs.py --all            # generate all specs -> SPECS/
    gen_specs.py --list           # list package names
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Share:
    """Shared data installation paths."""

    src: str
    dst: str


@dataclass
class Package:
    """Package metadata from pkgs.json."""

    name: str
    eco: str
    host: str
    slug: str = ""
    enabled: Any = None  # Can be bool or string
    prio: int = 5
    ver: str = ""
    bins: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    moddir: str = ""
    tagp: str = ""
    fallback: str = ""
    br: list[str] = field(default_factory=list)
    req: list[str] = field(default_factory=list)
    interp: str = "bash"
    cdir: str = ""
    pkg: str = ""
    url: str = ""
    summary: str = ""
    license: str = "MIT"
    note: str = ""
    mirror: str = ""
    exp: bool = False
    noman: bool = False
    autoreconf: bool = False
    cgo: bool = False
    gem_git: bool = False
    gpkg: str = "."
    npmbin: str = ""
    entry: str = "cli.js"
    share: Share | None = None
    build_cmd: str = "true"
    build_env: list[str] = field(default_factory=list)
    install_cmd: str = "%make_install"
    script_src: dict[str, str] = field(default_factory=dict)
    pbr_exclude: list[str] = field(default_factory=list)
    extra_files: list[str] = field(default_factory=list)
    topdir: str = ""
    prep_extra: str = ""

    def is_enabled(self) -> bool:
        """Check if package is enabled."""
        if self.enabled is None:
            return True
        if isinstance(self.enabled, bool):
            return self.enabled
        if isinstance(self.enabled, str):
            return self.enabled not in ("false", "0")
        return True


@dataclass
class PkgsFile:
    """Root structure of pkgs.json."""

    project: dict[str, Any]
    packages: list[Package]


def load_pkgs(path: Path) -> PkgsFile:
    """Load and parse pkgs.json.

    Args:
        path: Path to pkgs.json file.

    Returns:
        Parsed package definitions.

    Raises:
        FileNotFoundError: If file doesn't exist.
        ValueError: If JSON is invalid.
    """
    import json

    data = json.loads(path.read_text())

    packages = []
    for p in data.get("packages", []):
        # Handle share field
        share = None
        if p.get("share"):
            share = Share(src=p["share"]["src"], dst=p["share"]["dst"])

        packages.append(
            Package(
                name=p["name"],
                eco=p["eco"],
                host=p.get("host", ""),
                slug=p.get("slug", ""),
                enabled=p.get("enabled"),
                prio=p.get("prio", 5),
                ver=p.get("version", p.get("ver", "")),
                bins=p.get("bins", []),
                files=p.get("files", []),
                moddir=p.get("moddir", ""),
                tagp=p.get("tagp", ""),
                fallback=p.get("fallback", ""),
                br=p.get("br", []),
                req=p.get("req", []),
                interp=p.get("interp", "bash"),
                cdir=p.get("cdir", ""),
                pkg=p.get("pkg", ""),
                url=p.get("url", ""),
                summary=p.get("summary", ""),
                license=p.get("license", "MIT"),
                note=p.get("note", ""),
                mirror=p.get("mirror", ""),
                exp=p.get("exp", False),
                noman=p.get("noman", False),
                autoreconf=p.get("autoreconf", False),
                cgo=p.get("cgo", False),
                gem_git=p.get("gem_git", False),
                gpkg=p.get("gpkg", "."),
                npmbin=p.get("npmbin", ""),
                entry=p.get("entry", "cli.js"),
                share=share,
                build_cmd=p.get("build_cmd", "true"),
                build_env=p.get("build_env", []),
                install_cmd=p.get("install_cmd", "%make_install"),
                script_src=p.get("script_src", {}),
                pbr_exclude=p.get("pbr_exclude", []),
                extra_files=p.get("extra_files", []),
                topdir=p.get("topdir", ""),
                prep_extra=p.get("prep_extra", ""),
            )
        )

    return PkgsFile(project=data.get("project", {}), packages=packages)


def make_meta(pkgs: PkgsFile) -> dict[str, Package]:
    """Create name-to-Package mapping.

    Args:
        pkgs: Loaded package definitions.

    Returns:
        Dictionary mapping package names to Package objects.
    """
    return {p.name: p for p in pkgs.packages}


def esc(s: str) -> str:
    """Escape string for spec (currently passthrough)."""
    return s


# Baseline date for generated %changelog entries. Keep it aligned with the
# date stamped in already-committed SPECs so regeneration stays a no-op.
CHANGELOG_DATE_BASELINE = "Sat Sep 19 2026"


def _changelog_date() -> str:
    """Return a deterministic %changelog date for generated specs.

    Uses SOURCE_DATE_EPOCH (the reproducible-builds standard) when set, so
    builds can pin a concrete timestamp; otherwise falls back to a fixed
    baseline. Never uses today's wall-clock date, otherwise regenerating on a
    later UTC day would rewrite every SPEC's %changelog and break idempotency.
    """
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch:
        try:
            epoch_int = int(epoch)
            if epoch_int < 0:
                return CHANGELOG_DATE_BASELINE
            return datetime.datetime.fromtimestamp(
                epoch_int, tz=datetime.timezone.utc
            ).date().strftime("%a %b %d %Y")
        except (ValueError, OSError, OverflowError):
            pass
    return CHANGELOG_DATE_BASELINE


def esc_pct(s: str) -> str:
    """Escape percent signs for RPM spec."""
    return s.replace("%", "%%")


# openSUSE Tumbleweed rename map. This repo targets openSUSE only, so
# Fedora-specific package names are translated to their openSUSE equivalents.
SUSE_NAME_MAP = {
    "cargo-rpm-macros": "cargo-packaging",
    "pyproject-rpm-macros": "python-rpm-macros",
    "python3-devel": "python313-devel",
    "python3": "python313",
    "golang": "go",
    "nodejs": "nodejs-default",
    "ruby(release)": "ruby",
    "rubygems-devel": "ruby-devel",
    "python3-dbus": "python313-dbus-python",
    "libusb1-devel": "libusb-1_0-devel",
    "libjpeg-turbo-devel": "libjpeg8-devel",
    "glslang": "glslang-devel",
}


def suse(name: str) -> str:
    """Translate a Fedora package name to its openSUSE equivalent."""
    if name in SUSE_NAME_MAP:
        return SUSE_NAME_MAP[name]
    # Tumbleweed's default python3 (primary) is 3.13: python3-<mod> maps to python313-<mod>.
    if name.startswith("python3-"):
        return "python313-" + name[len("python3-"):]
    return name


def header(m: Package, ver: str) -> list[str]:
    """Generate spec header lines.

    Args:
        m: Package metadata.
        ver: Version string.

    Returns:
        List of header lines.
    """
    lic = m.license
    fixme = ""
    if lic.endswith("?"):
        lic = lic.rstrip("?")
        fixme = "# FIXME: проверить лицензию"

    url_map = {
        "github": f"https://github.com/{m.slug}",
        "codeberg": f"https://codeberg.org/{m.slug}",
        "gitlab": f"https://gitlab.com/{m.slug}",
        "pypi": f"https://pypi.org/project/{m.pkg or m.name}",
        "npm": f"https://www.npmjs.com/package/{m.pkg or m.name}",
    }
    url = url_map.get(m.host, m.url or "https://example.com")

    srcs = ["Source0:        %{name}-%{version}.tar.gz"]
    if m.eco == "cargo":
        srcs.append("Source1:        %{name}-vendor-%{version}.tar.gz")
    elif m.eco in ("go", "npm"):
        srcs.append("Source1:        %{name}-node-vendor-%{version}.tar.gz")

    lines = [
        f"Name:           {m.name}",
        f"Version:        {ver}",
        "Release:        1%{?dist}",
        f"Summary:        {esc(m.summary or m.name)}",
    ]

    if m.exp:
        lines.append(
            "# ВНИМАНИЕ: экспериментальная сборка, может падать на отдельных архитектурах"
        )
    if fixme:
        lines.append(fixme)

    lines += ["", f"License:        {lic}", f"URL:            {url}"] + srcs
    # openSUSE does not define %_licensedir (Fedora-only) — provide a fallback.
    lines.append("%{!?_licensedir:%global _licensedir %{_datadir}/licenses}")
    lines.append("%global debug_package %{nil}")
    lines.append("%global _unpackaged_files_terminate_build 0")
    lines.append("")

    return lines


def prep(m: Package) -> str:
    """Generate %prep section.

    Args:
        m: Package metadata.

    Returns:
        %prep section content.
    """
    import os

    d = os.environ.get("CANDY_TOPDIR") or m.topdir
    if not d:
        tag = m.tagp + "%{version}"
        d_map = {"gitlab": f"%{{name}}-{tag}", "npm": "package"}
        d = d_map.get(m.host, "%{name}-%{version}")

    n = "-N" if m.eco in ("cargo", "go") else "-p1"
    extra = " -a1" if m.eco in ("cargo", "go", "npm") else ""

    out = [f"%prep\n%autosetup {n}{extra} -n {d}"]
    if getattr(m, "prep_extra", ""):
        for cmd in m.prep_extra.splitlines():
            if cmd.strip():
                out.append(cmd)
    return "\n".join(out)


def add_br_req(out: list[str], br: list[str], req: list[str]) -> None:
    """Add BuildRequires and Requires lines (openSUSE names), dedup'd."""
    seen: set[str] = set()
    for x in br:
        n = suse(x)
        if n not in seen:
            seen.add(n)
            out.append(f"BuildRequires:  {n}")
    for x in req:
        n = suse(x)
        if n not in seen:
            seen.add(n)
            out.append(f"Requires:       {n}")


def body_script(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for script ecosystem."""
    extra_br_map: dict[str, list[str]] = {
        "perl": [],
        "ruby": [],
        "bash": [],
        "sh": ["coreutils"],
        "zsh": [],
        "pwsh": [],
        "python3": [],
    }
    br = br + extra_br_map.get(m.interp, [])

    out = ["BuildArch:      noarch"]
    add_br_req(out, br, req)
    out += [
        "",
        prep(m),
        "",
        "%build",
        "# чистый скрипт, сборка не требуется",
        "",
        "%install",
    ]

    targets = m.files
    import os

    p_ = os.environ.get("CANDY_FILELIST_PATH", "")
    fl: list[str] = []
    if p_ and os.path.exists(p_):
        fl = [l for l in Path(p_).read_text().split("\n") if l]

    for f in targets:
        base = f.split("/")[-1]
        if fl:
            exact = [l for l in fl if l == f or l.endswith("/" + base)]
            if exact:
                f = exact[0]
            else:
                stem = base.rsplit(".", 1)[0]
                fuzzy = [l for l in fl if stem in l.split("/")[-1]]
                if fuzzy:
                    out.append(f"# AUTO-FIXED: {base} -> {fuzzy[0]}")
                    f = fuzzy[0]
                else:
                    out.append(f"# WARNING: '{base}' нет в тарболе — пропущено")
                    continue
        out.append(f"install -Dpm0755 {f} %{{buildroot}}%{{_bindir}}/{base}")

    if m.share:
        src = m.share.src
        base = src.rstrip("/").split("/")[-1]
        if base != "." and "." in base and not src.endswith("/"):
            out += [f"install -Dpm0644 {src} %{{buildroot}}{m.share.dst}/{base}"]
        else:
            out += [
                f"mkdir -p %{{buildroot}}{m.share.dst}",
                f"cp -r {src}/. %{{buildroot}}{m.share.dst}/",
            ]

    out += ["", "%files", "%license LICENSE* COPYRIGHT*", "%doc README*"]
    for f in targets:
        out.append(f"%{{_bindir}}/{f.split('/')[-1]}")
    if m.share:
        out.append(m.share.dst)

    return "\n".join(out) + "\n"


def body_python_pkg(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for python-pkg ecosystem (openSUSE-native)."""
    br = ["python3-devel", "python3-pip", "python3-wheel", "python3-setuptools", "pyproject-rpm-macros"] + br
    out: list[str] = []
    out += [
        # openSUSE pyproject macros iterate over all co-installable flavors
        # (currently python314 + python313); build only the primary python3
        # to avoid pulling an unrequested interpreter into the build lane.
        "%global skip_python314 1",
    ]
    add_br_req(out, br, req)

    out += [
        "",
        prep(m),
        "",
        "%build",
        "%pyproject_wheel",
        "",
        "%install",
        "%pyproject_install",
        "",
        "%files",
        "%{python3_sitelib}/*",
    ] + m.extra_files

    return "\n".join(out) + "\n"


def body_python_script(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for python-script ecosystem."""
    br = ["python3"] + br
    out = ["BuildArch:      noarch"]
    add_br_req(out, br, req)
    out.append(f"Requires:       {suse('python3')}")
    out += [
        "",
        prep(m),
        "",
        "%build",
        "# интерпретируемый модуль, сборки нет",
        "",
        "%install",
    ]

    if m.moddir:
        out += [
            "mkdir -p %{buildroot}%{python3_sitelib}",
            f"cp -r {m.moddir} %{{buildroot}}%{{python3_sitelib}}/",
        ]

    for b in m.bins:
        src = m.script_src.get(b) or (
            b if not m.moddir else f"{m.moddir.rstrip('/')}/{b}"
        )
        out.append(f"install -Dpm0755 {src} %{{buildroot}}%{{_bindir}}/{b}")

    out += ["", "%files", "%license LICENSE*", "%doc README*"]
    if m.moddir:
        out.append("%{python3_sitelib}/" + m.moddir.strip("/") + "/")
    for b in m.bins:
        out.append(f"%{{_bindir}}/{b}")

    return "\n".join(out) + "\n"


def body_cargo(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for cargo ecosystem (openSUSE: без %cargo_prep/%cargo_build).

    openSUSE Tumbleweed не определяет Fedora-макросы %cargo_prep/%cargo_build/
    %cargo_install (cargo-packaging). Поэтому: vendor-источники через
    .cargo/config.toml + cargo build --release --offline + ручной install.
    """
    br = ["cargo", "rust", "gcc", "gcc-c++"] + br
    out: list[str] = []
    add_br_req(out, br, req)

    cd_b = f"cd {m.cdir}\n" if m.cdir else ""
    envs = "".join(f"export {e}\n" for e in m.build_env)
    bins = m.bins or ["%{name}"]
    inst = "\n".join(
        f"install -Dpm0755 target/release/{b} %{{buildroot}}%{{_bindir}}/{b}"
        for b in bins
    )
    # В workspace-репозиториях (cdir — член workspace) cargo кладёт бинарники
    # в target/ корня workspace, а не в target/ подкаталога crate. Пакеты с
    # таким layout переопределяют install_cmd (например "cd ..\ninstall ...").
    if m.install_cmd and m.install_cmd != "%make_install":
        inst = m.install_cmd

    out += [
        "",
        prep(m),
        f"mkdir -p .cargo",
        "cat > .cargo/config.toml <<'EOF'",
        '[source.crates-io]',
        'replace-with = "vendored-sources"',
        "",
        "[source.vendored-sources]",
        'directory = "vendor"',
        "EOF",
        "",
        "%build",
        cd_b + envs + "cargo build --release --offline",
        "",
        "%install",
        cd_b + inst,
        "",
        "%files",
        "%license LICENSE* COPYRIGHT*",
        "%doc README*",
    ]

    for b in bins:
        out.append(f"%{{_bindir}}/{b}")

    return "\n".join(out) + "\n"


def body_go(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for Go ecosystem."""
    br = ["golang"] + br
    out: list[str] = []
    add_br_req(out, br, req)

    bins = m.bins or ["%{name}"]
    cgo = "" if m.cgo else "export CGO_ENABLED=0"

    out += [
        "",
        prep(m),
        "",
        "%build",
        "export GOFLAGS='-mod=vendor'",
        cgo,
        "export GOPATH=$(mktemp -d)",
        "export GOCACHE=$GOPATH/cache",
        f"go build -trimpath -ldflags '-s -w' -o {bins[0]} {m.gpkg}",
        "",
        "%install",
    ]

    for b in bins:
        out.append(f"install -Dpm0755 {b} %{{buildroot}}%{{_bindir}}/{b}")

    out += ["", "%files", "%license LICENSE*", "%doc README*"]
    for b in bins:
        out.append(f"%{{_bindir}}/{b}")

    return "\n".join(out) + "\n"


def body_npm(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for npm ecosystem."""
    br = ["nodejs"] + br
    out = ["BuildArch:      noarch"]
    add_br_req(out, br, req)

    name = m.name
    libdir = "%{_prefix}/lib/" + name
    nb = m.npmbin or name

    out += [
        "",
        prep(m),
        "",
        "%build",
        "# bundled node_modules, сборка не требуется",
        "",
        "%install",
        f"mkdir -p %{{buildroot}}{libdir}",
        f"cp -a . %{{buildroot}}{libdir}/",
    ]

    if nb:
        out.append("mkdir -p %{buildroot}%{_bindir}")
        out.append(f"ln -sf ../lib/{name}/{m.entry} %{{buildroot}}%{{_bindir}}/{nb}")

    out += ["", "%files", "%license LICENSE*", "%doc README*", libdir]
    if nb:
        out.append(f"%{{_bindir}}/{nb}")

    return "\n".join(out) + "\n"


def body_gem(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for gem ecosystem (openSUSE-native, gem2rpm-style)."""
    br = ["ruby(release)", "rubygems-devel", "ruby"] + br
    out: list[str] = []
    mod_name = m.name
    out += [
        f"%define mod_name {mod_name}",
        "%define mod_full_name %{mod_name}-%{version}",
    ]
    add_br_req(out, br, req)

    out += ["", prep(m), "", "%build"]
    if m.gem_git:
        out.append(
            "git init -q . && git config user.email b@b.c && git config user.name b && git add -A && git commit -qm init"
        )
    out += [
        "# Create missing files referenced by gemspec",
        "for f in man/*.1 zsh/_*; do [ -f \"$f\" ] || touch \"$f\" 2>/dev/null || :; done",
        "gem build *.gemspec",
        "# gem_install.sh ищет .gem по шаблону */*.gem от buildsubdir — кладём в подкаталог",
        "mkdir -p gem-built && mv -- *.gem gem-built/",
        "",
        "%install",
        "%gem_install --no-rdoc --no-ri --symlink-binaries -f",
        "",
        "%files",
        "%gem_packages",
    ] + m.extra_files

    return "\n".join(out) + "\n"


def body_c(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for C ecosystems."""
    eco = m.eco

    if eco == "c-autotools":
        br = ["gcc", "make"] + br
        boot = ""
        if m.autoreconf:
            br += ["autoconf", "automake", "gettext", "libtool"]
            boot = "autoreconf -vfi\n"

        out: list[str] = []
        add_br_req(out, br, req)
        out += [
            "",
            prep(m),
            "",
            "%build",
            'export CFLAGS="${CFLAGS:-$RPM_OPT_FLAGS} -Wno-error=format-security"',
            boot + "%configure",
            "%make_build",
            "",
            "%install",
            "%make_install",
            "find %{buildroot} -name '*.la' -delete",
            "",
            "%files",
            f"%{{_bindir}}/{m.name}",
            "%{_mandir}/*",
        ]

    elif eco == "c-cmake":
        br = ["cmake", "gcc-c++"] + br
        out = []
        add_br_req(out, br, req)
        out += [
            "",
            prep(m),
            "",
            "%build",
            'export CFLAGS="${CFLAGS:-$RPM_OPT_FLAGS} -Wno-error=format-security"',
            "%cmake",
            "%cmake_build",
            "",
            "%install",
            "%cmake_install",
            "",
            "%files",
            "%{_bindir}/*",
            "%{_mandir}/*",
        ]

    else:  # c-make
        br = ["gcc", "make"] + br
        out = []
        add_br_req(out, br, req)
        out += [
            "",
            prep(m),
            "",
            "%build",
            'export CFLAGS="${CFLAGS:-$RPM_OPT_FLAGS} -Wno-error=format-security"',
            "%make_build",
            "",
            "%install",
            m.install_cmd,
            "",
            "%files",
            f"%{{_bindir}}/{m.bins[0] if m.bins else m.name}",
        ]
        if not m.noman:
            out.append("%{_mandir}/*")

    return "\n".join(out) + "\n"


def body_nim(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for Nim ecosystem."""
    br = ["nim"] + br
    out: list[str] = []
    add_br_req(out, br, req)

    cmd = m.build_cmd or f"nim c -d:release --out:{m.name} src/{m.name}.nim"
    out += [
        "",
        prep(m),
        "",
        "%build",
        cmd,
        "",
        "%install",
        f"install -Dpm0755 {m.name} %{{buildroot}}%{{_bindir}}/{m.name}",
        "",
        "%files",
        "%license LICENSE*",
        f"%{{_bindir}}/{m.name}",
    ]

    return "\n".join(out) + "\n"


def body_meson(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for Meson ecosystem."""
    br = ["meson", "gcc"] + br
    out: list[str] = []
    add_br_req(out, br, req)
    out += [
        "",
        prep(m),
        "",
        "%build",
        "%meson",
        "%meson_build",
        "",
        "%install",
        "%meson_install",
        "",
        "%files",
        "%{_bindir}/*",
    ]

    return "\n".join(out) + "\n"


def body_zig(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for Zig ecosystem."""
    out: list[str] = []
    add_br_req(out, ["zig"] + br, req)
    out += [
        "",
        prep(m),
        "",
        "%build",
        "zig build -Doptimize=ReleaseSafe",
        "",
        "%install",
        "mkdir -p %{buildroot}%{_bindir}",
        "cp -r zig-out/bin/. %{buildroot}%{_bindir}/",
        "",
        "%files",
        "%license LICENSE*",
        "%doc README*",
    ]

    for b in m.bins or ["%{name}"]:
        out.append(f"%{{_bindir}}/{b}")

    return "\n".join(out) + "\n"


def body_custom(m: Package, br: list[str], req: list[str]) -> str:
    """Generate body for custom ecosystem."""
    out: list[str] = []
    add_br_req(out, br, req)
    out += [
        "",
        prep(m),
        "",
        "%build",
        m.build_cmd,
        "",
        "%install",
    ]

    for b in m.bins or []:
        out.append(f"install -Dpm0755 {b} %{{buildroot}}%{{_bindir}}/{b}")

    if m.share:
        src = m.share.src
        base = src.rstrip("/").split("/")[-1]
        if base != "." and "." in base and not src.endswith("/"):
            out += [f"install -Dpm0644 {src} %{{buildroot}}{m.share.dst}/{base}"]
        else:
            out += [
                f"mkdir -p %{{buildroot}}{m.share.dst}",
                f"cp -r {src}/. %{{buildroot}}{m.share.dst}/",
            ]

    out += ["", "%files", "%license LICENSE* COPYRIGHT*", "%doc README*"]
    for b in m.bins or []:
        out.append(f"%{{_bindir}}/{b}")
    if m.share:
        out.append(m.share.dst)

    return "\n".join(out) + "\n"


# Ecosystem body generators
BODIES: dict[str, Any] = {
    "script": body_script,
    "python-pkg": body_python_pkg,
    "python-script": body_python_script,
    "cargo": body_cargo,
    "go": body_go,
    "npm": body_npm,
    "gem": body_gem,
    "c-autotools": body_c,
    "c-cmake": body_c,
    "c-make": body_c,
    "nim": body_nim,
    "meson": body_meson,
    "custom": body_custom,
    "zig": body_zig,
}


def render(name: str, ver: str, meta: dict[str, Package]) -> str:
    """Render complete RPM spec for a package.

    Args:
        name: Package name.
        ver: Version string.
        meta: Package metadata dictionary.

    Returns:
        Complete RPM spec content.

    Raises:
        KeyError: If package not found in metadata.
    """
    m = meta[name]
    head_lines = header(m, ver)
    br = list(m.br)
    req = list(m.req)

    body_fn = BODIES.get(m.eco)
    if body_fn is None:
        raise ValueError(f"Unknown ecosystem: {m.eco}")
    body = body_fn(m, br, req)

    desc = m.summary or name
    note = m.note
    # Deterministic %changelog date: prefers SOURCE_DATE_EPOCH (reproducible
    # builds) and otherwise falls back to a FIXED baseline. Using the wall-clock
    # date here breaks idempotency across day boundaries (regenerating specs on
    # the next UTC day rewrites every %changelog line). SOURCE_DATE_EPOCH is the
    # standard knob rpmbuild already honours for reproducible timestamps.
    today = _changelog_date()

    # License installation
    lic_inst = (
        "\nmkdir -p %{buildroot}%{_licensedir}/%{name}\n"
        "for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do "
        '[ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done'
    )

    if "\n%files" in body:
        body = body.replace("\n%files", lic_inst + "\n%files", 1)

    body = re.sub(r"^%license .*$", "%{_licensedir}/%{name}", body, flags=re.MULTILINE)
    body = re.sub(r"^%doc .*$", "", body, flags=re.MULTILINE)

    # Split tags and sections
    tags, _, secs = body.partition("\n%prep")
    if _:
        secs = "%prep" + secs
    else:
        tags, secs = body, ""

    parts = ["\n".join(head_lines), "", tags.strip()]

    if note:
        parts += ["", f"# NOTE: {esc_pct(note)}"]

    desc_block = [esc_pct(desc), ""]
    if m.url:
        desc_block += [
            "Официальный способ установки от апстрима / Upstream official install method:",
            f"  {esc_pct(m.url)}",
            "",
        ]

    desc_block += [
        "ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse-beta.",
        "Репозиторий в активной разработке — возможны поломки и резкие изменения.",
        "Помидорами не кидайтесь, лучше заводите issue.",
        "",
        "WARNING: this package comes from an UNOFFICIAL third-party repository",
        "(arcticlore/candy-opensuse-beta). Work-in-progress: expect breakage and sudden changes.",
        "Don't throw tomatoes - file issues instead.",
    ]

    parts += ["", "%description"] + desc_block

    if secs:
        parts += ["", secs.rstrip()]

    parts += [
        "",
        "%changelog",
        f"* {today} candy-bot <candy@localhost> - {ver}-1",
        "- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)",
        "",
    ]

    return "\n".join(parts)


def main() -> None:
    """Main entry point."""
    ap = argparse.ArgumentParser(description="RPM spec generator")
    ap.add_argument("name", nargs="?")
    ap.add_argument("version", nargs="?", default="0")
    ap.add_argument("--all", action="store_true", help="Generate all specs")
    ap.add_argument("--list", action="store_true", help="List package names")
    a = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    pkgs = load_pkgs(root / "pkgs.json")
    meta = make_meta(pkgs)

    if a.list:
        print("\n".join(meta.keys()))
        return

    if a.all:
        out_dir = root / "SPECS"
        out_dir.mkdir(exist_ok=True)
        ok = bad = 0
        state_path = root / "state" / "state.json"
        state = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except Exception:
                pass
        for n, m in meta.items():
            if not m.is_enabled():
                continue
            try:
                ver = state.get(n, {}).get("ver", "") or m.ver or "0"
                (out_dir / f"{n}.spec").write_text(render(n, ver, meta))
                ok += 1
            except (KeyError, ValueError, OSError) as e:
                print(f"[FAIL] {n}: {e}", file=sys.stderr)
                bad += 1
        print(f"сгенерировано: {ok}, ошибок: {bad}")
        return

    if not a.name:
        ap.error("нужен NAME или --all/--list")

    sys.stdout.write(render(a.name, a.version, meta))


if __name__ == "__main__":
    main()
