Name:           video-to-ascii
Version:        1.3.1
Release:        1%{?dist}
Summary:        Играть видео прямо в терминале ASCII-символами

License:        MIT
URL:            https://pypi.org/project/video-to-ascii
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  python313-devel
BuildRequires:  python-rpm-macros

# NOTE: нужны ffmpeg и portaudio в системе

%description
Играть видео прямо в терминале ASCII-символами

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse-beta.
Репозиторий в активной разработке — возможны поломки и резкие изменения.
Помидорами не кидайтесь, лучше заводите issue.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse-beta). Work-in-progress: expect breakage and sudden changes.
Don't throw tomatoes - file issues instead.

%prep
%autosetup -p1 -n video_to_ascii-1.3.1

%build
%pyproject_wheel

%install
%pyproject_install

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{python3_sitelib}/*
%{_bindir}/video-to-ascii

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 1.3.1-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
