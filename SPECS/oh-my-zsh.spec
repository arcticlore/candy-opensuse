Name:           oh-my-zsh
Version:        20260926.74965c9
Release:        1%{?dist}
Summary:        Framework for managing zsh configuration with 300+ plugins

License:        MIT
URL:            https://github.com/ohmyzsh/ohmyzsh
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
Requires:       zsh

%description
Framework for managing zsh configuration with 300+ plugins

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n ohmyzsh-74965c96098134b192f00084f966b4b02438a739

%build
# чистый скрипт, сборка не требуется

%install
mkdir -p %{buildroot}/usr/share/oh-my-zsh
cp -r ./. %{buildroot}/usr/share/oh-my-zsh/

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

/usr/share/oh-my-zsh

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 20260926.74965c9-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
