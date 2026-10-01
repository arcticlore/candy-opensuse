Name:           tetro-tui
Version:        3.6.2
Release:        1%{?dist}
Summary:        Терминальный тетрис с настройками

License:        MIT
URL:            https://github.com/Strophox/tetro-tui
Source0:        %{name}-%{version}.tar.gz
Source1:        %{name}-vendor-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  cargo
BuildRequires:  rust
BuildRequires:  gcc
BuildRequires:  gcc-c++

%description
Терминальный тетрис с настройками

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 137 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -N -a1 -n tetro-tui-v3.6.2
mkdir -p .cargo
cat > .cargo/config.toml <<'EOF'
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "vendor"
EOF

%build
cargo build --release --offline

%install
install -Dpm0755 target/release/tetro-tui %{buildroot}%{_bindir}/tetro-tui

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICEN[CS]E* COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%license %{_licensedir}/%{name}/LICENSE

%{_bindir}/tetro-tui

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 3.6.2-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
