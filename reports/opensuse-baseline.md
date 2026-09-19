# Baseline: arcticlore/candy-opensuse-beta

Снят: 2026-09-19 14:06:00 UTC  
Chroot, объявленные в проекте: opensuse-tumbleweed-x86_64, opensuse-tumbleweed-aarch64  
Всего chroot-вхождений в билдах: {'opensuse-tumbleweed-x86_64': 273, 'opensuse-tumbleweed-aarch64': 273, 'opensuse-leap-16.0-x86_64': 143, 'opensuse-leap-16.0-aarch64': 143}  
Билдов с включённым Leap: 143  

## Счётчики

- enabled в pkgs.json: **128**
- есть билды в COPR: **123**
- enabled без билдов (missing): **5** → `fend, pure, tabiew, wtfutil, zenith`
- последний билд succeeded: **54** / failed: **69**
- хоть раз succeeded-когда-либо: **57**
- никогда не собирался зелёным: **71**

## Причины фейлов (последние билды)

- `build-phase-error`: **65**
- `builddep-unresolved`: **4**

## Незарезолвленные BuildRequires (по логам)

| BR | пакетов с фейлом на нём |
|----|----|
| `glslang` | 1 |
| `python3-dbus` | 1 |
| `libusb1-devel` | 1 |
| `libjpeg-turbo-devel` | 1 |

## Фейлы по пакетам

| пакет | build | версия | причина | missing_br |
|-------|-------|--------|---------|------------|
| CrabFetch | 11003636 | 0.5.4-1 | build-phase-error |  |
| PyBonsai | 11003658 | 3.0.0-1 | build-phase-error |  |
| animfetch | 11003637 | 0.1.6-1 | build-phase-error |  |
| artem | 11003677 | 3.0.0-1 | build-phase-error |  |
| bacon | 11003617 | 3.25.0-1 | build-phase-error |  |
| bandwhich | 11003589 | 0.23.1-1 | build-phase-error |  |
| bottom | 11003590 | 0.14.9-1 | build-phase-error |  |
| broot | 11003591 | 1.60.1-1 | build-phase-error |  |
| cbeams | 11003651 | 1.0.1-1 | build-phase-error |  |
| choose | 11003684 | 1.3.7-1 | build-phase-error |  |
| colorls | 11003709 | 1.5.0-1 | build-phase-error |  |
| csview | 11003592 | 1.3.4-1 | build-phase-error |  |
| delta | 11003593 | 0.19.2-1 | build-phase-error |  |
| dua | 11003619 | 2.45.0-1 | build-phase-error |  |
| durdraw | 11003679 | 0.30.1-1 | build-phase-error |  |
| dysk | 11003620 | 3.7.0-1 | build-phase-error |  |
| flavours | 11003685 | 0.7.1-1 | build-phase-error |  |
| genact | 11003653 | 1.5.1-1 | build-phase-error |  |
| ghfetch | 11003626 | 20260919.4b44a4f-1 | build-phase-error |  |
| gitfetch | 11003627 | 0.1.1-1 | build-phase-error |  |
| gittype | 11003703 | 0.10.2-1 | build-phase-error |  |
| gitui | 11003628 | 0.28.1-1 | build-phase-error |  |
| gping | 11003594 | 1.21.0-1 | build-phase-error |  |
| grex | 11003629 | 1.4.6-1 | build-phase-error |  |
| himalaya | 11003694 | 2.1.0-1 | build-phase-error |  |
| hyperfine | 11003595 | 1.20.0-1 | build-phase-error |  |
| joshuto | 11003654 | 0.9.9-1 | build-phase-error |  |
| just | 11003598 | 1.58.0-1 | build-phase-error |  |
| kondo | 11003630 | 0.9-1 | build-phase-error |  |
| linuxwave | 11003687 | 0.4.0-1 | build-phase-error |  |
| lolcrab | 11003655 | 0.4.1-1 | build-phase-error |  |
| macchina | 11003631 | 6.4.0-1 | build-phase-error |  |
| maze | 11003672 | 20260919.eb99e65-1 | build-phase-error |  |
| mise | 11003599 | 2026.9.11-1 | build-phase-error |  |
| navi | 11003632 | 2.24.0-1 | build-phase-error |  |
| oha | 11003603 | 1.16.0-1 | build-phase-error |  |
| onefetch | 11003633 | 2.28.1-1 | build-phase-error |  |
| pipes.rs | 11003656 | 1.6.4-1 | build-phase-error |  |
| pokemon-icat | 11003634 | 20260919.54d4bc5-1 | build-phase-error |  |
| presenterm | 11003635 | 0.16.1-1 | build-phase-error |  |
| pridefetch | 11003640 | 20260919.dc24d03-1 | build-phase-error |  |
| pywal | 11003689 | 3.8.15-1 | build-phase-error |  |
| rmpc | 11003704 | 0.11.0-1 | build-phase-error |  |
| sd | 11003606 | 1.1.0-1 | build-phase-error |  |
| snowmachine | 11003657 | 2.0.2-1 | build-phase-error |  |
| starship | 11003611 | 1.26.0-1 | build-phase-error |  |
| systeroid | 11003705 | 0.4.6-1 | build-phase-error |  |
| tealdeer | 11003612 | 1.9.0-1 | build-phase-error |  |
| terminaltexteffects | 11003659 | 0.15.0-1 | build-phase-error |  |
| termshot | 11003646 | 0.6.1-1 | build-phase-error |  |
| termusic | 11003700 | 0.13.2-1 | build-phase-error |  |
| toipe | 11003647 | 0.5.0-1 | build-phase-error |  |
| tokei | 11003613 | 15.0.0-1 | build-phase-error |  |
| trippy | 11003585 | 0.13.0-1 | build-phase-error |  |
| ttyper | 11003666 | 1.6.0-1 | build-phase-error |  |
| ttysvr | 11003650 | 0.3.4-1 | build-phase-error |  |
| unimatrix | 11003676 | 20260919.dff519f-1 | build-phase-error |  |
| viddy | 11003614 | 1.3.1-1 | build-phase-error |  |
| video-to-ascii | 11003693 | 1.3.1-1 | build-phase-error |  |
| viu | 11003681 | 1.6.1-1 | build-phase-error |  |
| wallust | 11003691 | 4.1.0~alpha-1 | build-phase-error |  |
| watchexec | 11003586 | 2.7.3-1 | build-phase-error |  |
| xh | 11003616 | 0.26.2-1 | build-phase-error |  |
| yazi | 11003588 | 26.8.15-1 | build-phase-error |  |
| zellij | 11003706 | 0.45.1-1 | build-phase-error |  |
| Rio | 11003710 | 0.5.28-1 | builddep-unresolved | glslang |
| archey4 | 11003639 | 4.15.0.0-1 | builddep-unresolved | python3-dbus |
| cyme | 11003652 | 3.0.2-1 | builddep-unresolved | libusb1-devel |
| jp2a | 11003680 | 1.3.3-1 | builddep-unresolved | libjpeg-turbo-devel |
