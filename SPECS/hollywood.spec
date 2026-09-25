Name:           hollywood
Version:        1.25
Release:        1%{?dist}
Summary:        Fill your console with Hollywood melodrama technobabble

License:        GPL-3.0-or-later
URL:            https://github.com/dustinkirkland/hollywood
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
Requires:       byobu
Requires:       ccze
Requires:       cmatrix
Requires:       oneko
Requires:       cowsay
Requires:       fortune-mod
Requires:       jp2a

%description
Fill your console with Hollywood melodrama technobabble

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
mkdir -p %{buildroot}/usr/libexec/hollywood
cp -r ./. %{buildroot}/usr/libexec/hollywood/

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

/usr/libexec/hollywood

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 1.25-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
