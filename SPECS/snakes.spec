Name:           snakes
Version:        20260926.57ee34b
Release:        1%{?dist}
Summary:        Snakes crawling across your terminal

License:        GPL-3.0-or-later
URL:            https://github.com/pipeseroni/snakes.pl
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
Requires:       perl

%description
Snakes crawling across your terminal

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n snakes.pl-57ee34b3283144c236a900c07cbfcca74858f12a

%build
# чистый скрипт, сборка не требуется

%install
install -Dpm0755 snake.pl %{buildroot}%{_bindir}/snake.pl

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/snake.pl

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 20260926.57ee34b-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
