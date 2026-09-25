Name:           cbeams
Version:        1.0.1
Release:        1%{?dist}
Summary:        Colorful animated beams in the terminal

License:        BSD-2-Clause
URL:            https://github.com/tartley/cbeams
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


%global skip_python314 1
BuildRequires:  python313-devel
BuildRequires:  python313-pip
BuildRequires:  python313-wheel
BuildRequires:  python313-setuptools
BuildRequires:  python-rpm-macros
Requires:       python313-colorama

%description
Colorful animated beams in the terminal

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n cbeams-1.0.1

%build
%pyproject_wheel

%install
%pyproject_install

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{python3_sitelib}/*

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 1.0.1-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
