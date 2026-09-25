Name:           parrotsay
Version:        0.1.0
Release:        1%{?dist}
Summary:        Party parrot says things in your terminal

License:        MIT
URL:            https://www.npmjs.com/package/parrotsay
Source0:        %{name}-%{version}.tar.gz
Source1:        %{name}-node-vendor-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
BuildRequires:  nodejs-default

%description
Party parrot says things in your terminal

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -a1 -n package

%build
# bundled node_modules, сборка не требуется

%install
mkdir -p %{buildroot}%{_prefix}/lib/parrotsay
cp -a . %{buildroot}%{_prefix}/lib/parrotsay/
mkdir -p %{buildroot}%{_bindir}
ln -sf ../lib/parrotsay/cli.js %{buildroot}%{_bindir}/parrotsay

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_prefix}/lib/parrotsay
%{_bindir}/parrotsay

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 0.1.0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
