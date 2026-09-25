# candy-opensuse

Порт проекта [candy-rpm](https://github.com/arcticlore/candy-rpm)
(terminal eye candy: fetch-инструменты, ASCII-анимации, современные CLI-замены)
на **openSUSE Tumbleweed и Leap 16.0** (x86_64 + aarch64).

Основной проект собирается под Fedora 43/44/45/rawhide:

- Репозиторий: https://github.com/arcticlore/candy-rpm
- COPR: https://copr.fedorainfracloud.org/coprs/arcticlore/candy/

Здесь — только openSUSE.

## Статус

- COPR-проект: https://copr.fedorainfracloud.org/coprs/arcticlore/candy-opensuse-beta/
- Чруты: `opensuse-tumbleweed-x86_64`, `opensuse-tumbleweed-aarch64`,
  `opensuse-leap-16.0-x86_64`, `opensuse-leap-16.0-aarch64`
- Сборка: GitHub Actions → COPR, автоматическое обновление.

## Установка (openSUSE Tumbleweed / Leap 16.0)

```sh
# нужен включённый репозиторий COPR (пакет dnf-plugins-core / dnf copr plugin)
sudo dnf copr enable arcticlore/candy-opensuse-beta
sudo dnf install starship   # или любой другой пакет проекта
```

## Как это устроено

- `pkgs.json` — список пакетов (тот же, что в основном проекте) + openSUSE-чруты.
- `bin/gen_specs.py` — генератор spec'ов, адаптированный под openSUSE:
  - определяет `%_licensedir` (в openSUSE его нет);
  - транслирует имена зависимостей Fedora → openSUSE
    (например `python3-devel` → `python311-devel`, `golang` → `go`,
    `cargo-rpm-macros` → `cargo-packaging`).
- `.github/workflows/update.yml` — ежедневно собирает SRPM'ы и отправляет их в COPR
  `arcticlore/candy-opensuse-beta`.

## Лицензия

См. [LICENSE](LICENSE).
<br><br>

> Код предоставляется «как есть». Автор сам уже не помнит, как он работает. Удачи!
