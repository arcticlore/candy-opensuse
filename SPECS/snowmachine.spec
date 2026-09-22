Name:           snowmachine
Version:        2.0.2
Release:        1%{?dist}
Summary:        Snow in your terminal

License:        MIT
URL:            https://pypi.org/project/snowmachine
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
BuildRequires:  python313-hatchling

%description
Snow in your terminal

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse-beta.
Репозиторий в активной разработке — возможны поломки и резкие изменения.
Помидорами не кидайтесь, лучше заводите issue.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse-beta). Work-in-progress: expect breakage and sudden changes.
Don't throw tomatoes - file issues instead.

%prep
%autosetup -p1 -n snowmachine-2.0.2

%build
%pyproject_wheel

%install
%pyproject_install

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{python3_sitelib}/*
%{_bindir}/snowmachine

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 2.0.2-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
