Name:           cpufetch
Version:        0
Release:        1%{?dist}
Summary:        ASCII-арт с информацией о процессоре

License:        GPL-2.0-only
URL:            https://github.com/Dr-Noob/cpufetch
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  gcc
BuildRequires:  make

%description
ASCII-арт с информацией о процессоре

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 161 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n %{name}-%{version}

%build
export CFLAGS="${CFLAGS:-$RPM_OPT_FLAGS} -Wno-error=format-security"
%make_build

%install
%make_install PREFIX=%{_prefix}

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICEN[CS]E* COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_bindir}/cpufetch
%{_mandir}/*

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
