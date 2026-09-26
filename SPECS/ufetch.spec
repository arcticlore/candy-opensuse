Name:           ufetch
Version:        20260926.19a71dc8
Release:        1%{?dist}
Summary:        Tiny system info for Unix-like operating systems

License:        GPL-3.0-or-later
URL:            https://gitlab.com/jschx/ufetch
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
BuildRequires:  coreutils
Requires:       sh

%description
Tiny system info for Unix-like operating systems

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n ufetch-19a71dc8

%build
# чистый скрипт, сборка не требуется

%install
install -Dpm0755 ufetch-alpine %{buildroot}%{_bindir}/ufetch-alpine

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/ufetch-alpine

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 20260926.19a71dc8-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
