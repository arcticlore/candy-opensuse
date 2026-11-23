Name:           scrap-engine
Version:        0
Release:        1%{?dist}
Summary:        A 2D ascii game engine for the terminal

License:        GPL-3.0-only
URL:            https://pypi.org/project/scrap-engine
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildArch:      noarch
Provides:       python3-scrap-engine
%global skip_python314 1
BuildRequires:  python313-devel
BuildRequires:  python313-pip
BuildRequires:  python313-wheel
BuildRequires:  python313-setuptools
BuildRequires:  python-rpm-macros

# NOTE: noarch, сборится из sdist PyPI; Provides python3-scrap-engine для Requires у pokete

%description
A 2D ascii game engine for the terminal

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 153 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n scrap_engine-1.5.4
rm -rf src/tests

%build
%pyproject_wheel

%install
%pyproject_install

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICEN[CS]E* COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{python3_sitelib}/*

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
