Name:           nyancat
Version:        1.2.1
Release:        1%{?dist}
Summary:        Анимированный Nyan Cat в терминале

License:        NCSA
URL:            https://github.com/klange/nyancat
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  gcc
BuildRequires:  make

# NOTE: корневой Makefile только делегирует в src/; install-цели нет — ставим бинарь вручную

%description
Анимированный Nyan Cat в терминале

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 153 пакетов для Tumbleweed и Leap 16.0; возможны редкие
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
install -Dpm0755 src/nyancat %{buildroot}%{_bindir}/nyancat

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICEN[CS]E* COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_bindir}/nyancat

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 1.2.1-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
