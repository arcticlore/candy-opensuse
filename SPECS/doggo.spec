Name:           doggo
Version:        1.4.0
Release:        1%{?dist}
Summary:        Fast command-line DNS client for humans

License:        GPL-3.0-only
URL:            https://github.com/mr-karan/doggo
Source0:        %{name}-%{version}.tar.gz
Source1:        %{name}-node-vendor-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  go

%description
Fast command-line DNS client for humans

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse-beta.
Репозиторий в активной разработке — возможны поломки и резкие изменения.
Помидорами не кидайтесь, лучше заводите issue.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse-beta). Work-in-progress: expect breakage and sudden changes.
Don't throw tomatoes - file issues instead.

%prep
%autosetup -N -a1 -n doggo-1.4.0

%build
export GOFLAGS='-mod=vendor'
export CGO_ENABLED=0
export GOPATH=$(mktemp -d)
export GOCACHE=$GOPATH/cache
go build -trimpath -ldflags '-s -w' -o doggo ./cmd/doggo

%install
install -Dpm0755 doggo %{buildroot}%{_bindir}/doggo

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/doggo

%changelog
* Fri Sep 18 2026 candy-bot <candy@localhost> - 1.4.0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
