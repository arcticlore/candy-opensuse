Name:           colorls
Version:        1.5.0
Release:        1%{?dist}
Summary:        Prettifies ls output with colors and font-awesome icons
# ВНИМАНИЕ: экспериментальная сборка, может падать на отдельных архитектурах

License:        MIT
URL:            https://github.com/athityakumar/colorls
Source0:        %{name}-%{version}.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0


%define mod_name colorls
%define mod_full_name %{mod_name}-%{version}
BuildRequires:  ruby
BuildRequires:  ruby-devel
BuildRequires:  git-core

%description
Prettifies ls output with colors and font-awesome icons

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse-beta.
Репозиторий в активной разработке — возможны поломки и резкие изменения.
Помидорами не кидайтесь, лучше заводите issue.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse-beta). Work-in-progress: expect breakage and sudden changes.
Don't throw tomatoes - file issues instead.

%prep
%autosetup -p1 -n %{name}-%{version}
mkdir -p zsh && touch man/colorls.1 zsh/_colorls

%build
git init -q . && git config user.email b@b.c && git config user.name b && git add -A && git commit -qm init
# Create missing files referenced by gemspec
for f in man/*.1 zsh/_*; do [ -f "$f" ] || touch "$f" 2>/dev/null || :; done
gem build *.gemspec
# gem_install.sh ищет .gem по шаблону */*.gem от buildsubdir — кладём в подкаталог
mkdir -p gem-built && mv -- *.gem gem-built/

%install
%gem_install --no-rdoc --no-ri --symlink-binaries -f

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%gem_packages

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 1.5.0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
