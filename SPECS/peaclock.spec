Name:           peaclock
Version:        0.4.3
Release:        1%{?dist}
Summary:        Часы/секундомер/таймер с цветными цифрами
# ВНИМАНИЕ: экспериментальная сборка, может падать на отдельных архитектурах

License:        MIT
URL:            https://github.com/octobanana/peaclock
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  gcc-c++
BuildRequires:  cmake
BuildRequires:  make
BuildRequires:  ncurses-devel
BuildRequires:  libicu-devel

%description
Часы/секундомер/таймер с цветными цифрами

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n %{name}-%{version}

%build
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build && cp build/peaclock .

%install
install -Dpm0755 peaclock %{buildroot}%{_bindir}/peaclock

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/peaclock

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 0.4.3-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
