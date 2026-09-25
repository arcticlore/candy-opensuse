Name:           arttime
Version:        2.5.0
Release:        1%{?dist}
Summary:        ASCII art, clock, timer and time manager for the terminal

License:        GPL-3.0-or-later
URL:            https://github.com/poetaman/arttime
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
BuildRequires:  python313-pytz
BuildRequires:  python313-rich
BuildRequires:  python313-tomli-w

%description
ASCII art, clock, timer and time manager for the terminal

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
# чистый скрипт, сборка не требуется

%install
install -Dpm0755 bin/arttime %{buildroot}%{_bindir}/arttime
install -Dpm0755 bin/artprint %{buildroot}%{_bindir}/artprint

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/arttime
%{_bindir}/artprint

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 2.5.0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
