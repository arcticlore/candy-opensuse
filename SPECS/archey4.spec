Name:           archey4
Version:        4.15.0.0
Release:        1%{?dist}
Summary:        Arch Linux system information tool (maintained fork)

License:        GPL-3.0-or-later
URL:            https://github.com/HorlogeSkynet/archey4
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  python314-devel
BuildRequires:  python314-pip
BuildRequires:  python314-wheel
BuildRequires:  python314-setuptools
BuildRequires:  python-rpm-macros
BuildRequires:  python314-dbus-python
BuildRequires:  python314-distro
BuildRequires:  python314-netifaces
BuildRequires:  python314-setproctitle

%description
Arch Linux system information tool (maintained fork)

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse-beta.
Репозиторий в активной разработке — возможны поломки и резкие изменения.
Помидорами не кидайтесь, лучше заводите issue.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse-beta). Work-in-progress: expect breakage and sudden changes.
Don't throw tomatoes - file issues instead.

%prep
%autosetup -p1 -n %{name}-%{version}

%build
%pyproject_wheel

%install
%pyproject_install

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{python3_sitelib}/*
%{_bindir}/archey
%{_docdir}/archey4

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 4.15.0.0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
