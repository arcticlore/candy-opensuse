Name:           ascsaver
Version:        20260926.cba337b5
Release:        1%{?dist}
Summary:        Collection of ASCII screensavers (dogs/globe/nasa/star_wars)

License:        GPL-2.0-only
URL:            https://gitlab.com/mezantrop/ascsaver
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
BuildRequires:  coreutils
Requires:       bash
Requires:       perl

%description
Collection of ASCII screensavers (dogs/globe/nasa/star_wars)

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n ascsaver-cba337b5

%build
# чистый скрипт, сборка не требуется

%install
mkdir -p %{buildroot}/usr/libexec/ascsaver
cp -r ./. %{buildroot}/usr/libexec/ascsaver/

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

/usr/libexec/ascsaver

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 20260926.cba337b5-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
