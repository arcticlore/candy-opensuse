Name:           pridefetch
Version:        20260926.dc24d03
Release:        1%{?dist}
Summary:        Neofetch, but gay

License:        GPL-3.0-or-later
URL:            https://github.com/cartoon-raccoon/pridefetch
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
BuildRequires:  python313
Requires:       python313

%description
Neofetch, but gay

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n pridefetch-dc24d03d5ea320010be7f69e77162156521c4460

%build
# интерпретируемый модуль, сборки нет

%install
install -Dpm0755 pridefetch %{buildroot}%{_bindir}/pridefetch

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/pridefetch

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 20260926.dc24d03-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
