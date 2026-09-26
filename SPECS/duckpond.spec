Name:           duckpond
Version:        20260926.00c96ca
Release:        1%{?dist}
Summary:        Ducks swimming in a pond, in your terminal

License:        GPL-3.0-or-later
URL:            https://github.com/gsobell/duckpond.sh
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
Requires:       bash

%description
Ducks swimming in a pond, in your terminal

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n duckpond.sh-00c96ca8a247be79fb961773770799fee5e54758

%build
# чистый скрипт, сборка не требуется

%install
install -Dpm0755 duckpond.sh %{buildroot}%{_bindir}/duckpond.sh

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/duckpond.sh

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 20260926.00c96ca-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
