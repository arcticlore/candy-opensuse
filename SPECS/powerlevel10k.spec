Name:           powerlevel10k
Version:        1.20.0
Release:        1%{?dist}
Summary:        Zsh theme focused on speed, flexibility and out-of-box UX

License:        MIT
URL:            https://github.com/romkatv/powerlevel10k
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
Requires:       zsh

%description
Zsh theme focused on speed, flexibility and out-of-box UX

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
mkdir -p %{buildroot}/usr/share/powerlevel10k
cp -r ./. %{buildroot}/usr/share/powerlevel10k/

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

/usr/share/powerlevel10k

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 1.20.0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
