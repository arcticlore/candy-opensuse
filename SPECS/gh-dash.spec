Name:           gh-dash
Version:        0
Release:        1%{?dist}
Summary:        Дашборд pull request'ов и issue прямо в терминале

License:        MIT
URL:            https://github.com/dlvhdr/gh-dash
Source0:        %{name}-%{version}.tar.gz
Source1:        %{name}-node-vendor-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


BuildRequires:  go

# NOTE: лицензия в корне называется LICENSE.txt — glob %%license LICENSE* её ловит

%description
Дашборд pull request'ов и issue прямо в терминале

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 174 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
%autosetup -N -a1 -n %{name}-%{version}

%build
export GOFLAGS='-mod=vendor'
export CGO_ENABLED=0
export GOPATH=$(mktemp -d)
export GOCACHE=$GOPATH/cache
go build -trimpath -ldflags '-s -w' -o gh-dash .

%install
install -Dpm0755 gh-dash %{buildroot}%{_bindir}/gh-dash

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICEN[CS]E* COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_licensedir}/%{name}

%{_bindir}/gh-dash

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
