# candy-opensuse-beta

**БЕТА.** Экспериментальный порт проекта [candy-rpm](https://github.com/arcticlore/candy-rpm)
(terminal eye candy: fetch-инструменты, ASCII-анимации, современные CLI-замены)
на **openSUSE Tumbleweed и Leap 16.0** (x86_64 + aarch64).

Основной (стабильный) проект живёт отдельно и собирается под Fedora 43/44/45/rawhide:

- Репозиторий: https://github.com/arcticlore/candy-rpm
- COPR: https://copr.fedorainfracloud.org/coprs/arcticlore/candy/

Здесь — только openSUSE. Когда набор пакетов стабилизируется, поддержку перенесём
в основной пайплайн (см. раздел «Перенос в основу»).

## Статус

- COPR-проект: https://copr.fedorainfracloud.org/coprs/arcticlore/candy-opensuse-beta/
- Чруты: `opensuse-tumbleweed-x86_64`, `opensuse-tumbleweed-aarch64`,
  `opensuse-leap-16.0-x86_64`, `opensuse-leap-16.0-aarch64`
- Пакеты могут не собираться и ломаться без предупреждения. Это ожидаемо.

## Установка (openSUSE Tumbleweed / Leap 16.0)

```sh
# нужен включённый репозиторий COPR (пакет dnf-plugins-core / dnf copr plugin)
sudo dnf copr enable arcticlore/candy-opensuse-beta
sudo dnf install starship   # или любой другой пакет проекта
```

## Как это устроено

- `pkgs.json` — список пакетов (тот же, что в основном проекте) + чруты беты.
- `bin/gen_specs.py` — генератор spec'ов, адаптированный под openSUSE:
  - определяет `%_licensedir` (в openSUSE его нет);
  - транслирует имена зависимостей Fedora → openSUSE
    (например `python3-devel` → `python311-devel`, `golang` → `go`,
    `cargo-rpm-macros` → `cargo-packaging`).
- `.github/workflows/update.yml` — ежедневно собирает SRPM'ы и отправляет их в COPR
  `arcticlore/candy-opensuse-beta`.

## Перенос в основу

Когда openSUSE-сборки станут стабильными, изменения из этого репозитория
переносятся в `candy-rpm` как мульти-дистрибутивный слой (условные макросы
`%if 0%{?suse_version}` и отдельный набор чрутов).

## Лицензия

См. [LICENSE](LICENSE).
