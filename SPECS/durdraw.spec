Name:           durdraw
Version:        0.30.1
Release:        1%{?dist}
Summary:        ANSI/ASCII and Unicode art editor with animation

License:        GPL-3.0-or-later
URL:            https://github.com/cmang/durdraw
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  python314-devel
BuildRequires:  python314-pip
BuildRequires:  python314-wheel
BuildRequires:  python-rpm-macros

%description
ANSI/ASCII and Unicode art editor with animation

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse-beta.
Репозиторий в активной разработке — возможны поломки и резкие изменения.
Помидорами не кидайтесь, лучше заводите issue.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse-beta). Work-in-progress: expect breakage and sudden changes.
Don't throw tomatoes - file issues instead.

%prep
%autosetup -p1 -n durdraw-0.30.1

%build
%pyproject_wheel

%install
%pyproject_install

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{python3_sitelib}/*
%{_bindir}/durdraw
%{_bindir}/durfetch
%{_bindir}/durview

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 0.30.1-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
