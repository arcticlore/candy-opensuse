Name:           rxfetch
Version:        20260919.5eb3582
Release:        1%{?dist}
Summary:        Custom system fetching tool written in bash

License:        MIT
URL:            https://github.com/mngshm/rxfetch
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
Requires:       bash

%description
Custom system fetching tool written in bash

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n rxfetch-5eb3582d90a688c8330d1a72c6ac4c1b1ccd3872

%build
# чистый скрипт, сборка не требуется

%install
install -Dpm0755 rxfetch %{buildroot}%{_bindir}/rxfetch
mkdir -p %{buildroot}/usr/share/rxfetch
cp -r ttf-material-design-icons/. %{buildroot}/usr/share/rxfetch/

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/rxfetch
/usr/share/rxfetch

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 20260919.5eb3582-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
