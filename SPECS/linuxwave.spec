Name:           linuxwave
Version:        0.4.0
Release:        1%{?dist}
Summary:        Generate music from the entropy of Linux

License:        Apache-2.0
URL:            https://github.com/orhun/linuxwave
Source0:        linuxwave-%{version}-x86_64-linux.tar.gz
Source1:        linuxwave-%{version}-aarch64-linux.tar.gz
%{!?_licensedir:%global _licensedir %{_datadir}/licenses}
%global debug_package %{nil}
%global _unpackaged_files_terminate_build 0




%description
Generate music from the entropy of Linux

ВНИМАНИЕ: пакет из неофициального стороннего репозитория arcticlore/candy-opensuse.
Серийная (stable) сборка 127 пакетов для Tumbleweed и Leap 16.0; возможны редкие
поломки на свежих релизах апстрима — заводите issue, помидоры не кидаем.

WARNING: this package comes from an UNOFFICIAL third-party repository
(arcticlore/candy-opensuse). Stable channel, auto-updated from upstream releases;
expect occasional breakage only on brand-new upstream versions — file issues instead.
Don't throw tomatoes.

%prep
# официальный prebuilt-ассет из апстрим-релиза — сборка из исходников
# не выполняется, сеть в buildroot не используется

%build
# сборка не требуется: статик-pie бинарник из официального релиза

%install
mkdir -p %{buildroot}%{_bindir}
case %{_arch} in
  x86_64) T="%{SOURCE0}";;
  aarch64) T="%{SOURCE1}";;
  *) echo "%{name}: unsupported arch %{_arch}" >&2; exit 1;;
esac
TMPD=$(mktemp -d)
tar -xzf "$T" -C "$TMPD"
install -Dm0755 "$TMPD/%{name}-%{version}/%{name}" %{buildroot}%{_bindir}/%{name}
install -Dm0644 "$TMPD/%{name}-%{version}/man/%{name}.1" %{buildroot}%{_mandir}/man1/%{name}.1
install -Dm0644 "$TMPD/%{name}-%{version}/LICENSE" %{buildroot}%{_licensedir}/%{name}/LICENSE
rm -rf "$TMPD"

mkdir -p %{buildroot}%{_licensedir}/%{name}
for f in LICENSE* LICEN[CS]E.MD COPYING* COPYRIGHT* NOTICE*; do [ -e "$f" ] && cp -p "$f" %{buildroot}%{_licensedir}/%{name}/ || true; done
%files
%{_bindir}/linuxwave
%{_licensedir}/%{name}
%{_mandir}/man1/%{name}.1*

%changelog
* Sat Sep 19 2026 candy-bot <candy@localhost> - 0.4.0-1
- Автосборка из апстрим-релиза (terminal-eye-candy pipeline)
