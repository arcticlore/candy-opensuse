Name:           tfire
Version:        0.3.0
Release:        1%{?dist}
Summary:        Fire animation in your terminal

License:        GPL-3.0-or-later
URL:            https://github.com/tech-chad/tfire
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
BuildRequires:  python313
Requires:       bash
Requires:       python313

%description
Fire animation in your terminal

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
# интерпретируемый модуль, сборки нет

%install
install -Dpm0755 tfire.py %{buildroot}%{_bindir}/tfire

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/tfire

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 0.3.0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
