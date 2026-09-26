Name:           rickrollrc
Version:        20260926.85d6893
Release:        1%{?dist}
Summary:        Rick Astley rickrolls your terminal

License:        MIT
URL:            https://github.com/keroserene/rickrollrc
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
Requires:       bash

%description
Rick Astley rickrolls your terminal

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n rickrollrc-85d6893495ae05f9154fe1528026916ca5354ca4

%build
# чистый скрипт, сборка не требуется

%install
install -Dpm0755 roll.sh %{buildroot}%{_bindir}/roll.sh

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/roll.sh

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 20260926.85d6893-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
