Name:           pscircle
Version:        1.4.0
Release:        1%{?dist}
Summary:        Visualize processes as a circular tree wallpaper

License:        GPL-2.0-or-later
URL:            https://gitlab.com/mildlyparallel/pscircle
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  meson
BuildRequires:  gcc
BuildRequires:  cairo-devel
BuildRequires:  glib2-devel
BuildRequires:  libpng-devel

%description
Visualize processes as a circular tree wallpaper

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -p1 -n %{name}-v%{version}

%build
%meson
%meson_build

%install
%meson_install

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_bindir}/*

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 1.4.0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
