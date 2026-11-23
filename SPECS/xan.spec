Name:           xan
Version:        0
Release:        1%{?dist}
Summary:        Нарезка, фильтрация и статистика по CSV в терминале

License:        Unlicense OR MIT
URL:            https://github.com/medialab/xan
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
Нарезка, фильтрация и статистика по CSV в терминале

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 153 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -N -a1 -n %{name}-%{version}
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
install -Dpm0755 target/release/xan %{buildroot}%{_bindir}/xan

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICEN[CS]E* COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/xan

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
