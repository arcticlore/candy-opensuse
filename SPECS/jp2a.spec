Name:           jp2a
Version:        1.3.3
Release:        1%{?dist}
Summary:        Convert JPG/PNG images to ASCII art

License:        GPL-2.0-only
URL:            https://github.com/Talinx/jp2a
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  gcc
BuildRequires:  make
BuildRequires:  autoconf
BuildRequires:  automake
BuildRequires:  libjpeg8-devel
BuildRequires:  libpng-devel
BuildRequires:  libwebp-devel
BuildRequires:  libexif-devel
BuildRequires:  gettext
BuildRequires:  libtool

%description
Convert JPG/PNG images to ASCII art

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
export CFLAGS="${CFLAGS:-$RPM_OPT_FLAGS} -Wno-error=format-security"
autoreconf -vfi
%configure
%make_build

%install
%make_install
find %{buildroot} -name '*.la' -delete

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_bindir}/jp2a
%{_mandir}/*

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 1.3.3-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
