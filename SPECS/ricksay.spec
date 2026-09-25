Name:           ricksay
Version:        20260919.e75f53d
Release:        1%{?dist}
Summary:        Rick and Morty quotes of the day (cowsay clone)

License:        MIT
URL:            https://github.com/kochie/ricksay
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  gcc
Requires:       bash
Requires:       cowsay

%description
Rick and Morty quotes of the day (cowsay clone)

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n ricksay-e75f53dcdc91baa8f2651328734e2ee7e6654663

%build
gcc -O2 src/main.c -o ricksay

%install
install -Dpm0755 ricksay %{buildroot}%{_bindir}/ricksay
install -Dpm0644 src/quotes.json %{buildroot}/usr/share/ricksay/quotes.json

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/ricksay
/usr/share/ricksay

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 20260919.e75f53d-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
