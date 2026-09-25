# candy-opensuse

Неофициальная **stable**-ветка порта проекта
[candy-rpm](https://github.com/arcticlore/candy-rpm)
(terminal eye candy: fetch-инструменты, ASCII-анимации, современные CLI-замены)
на **openSUSE Tumbleweed и Leap 16.0** (x86_64 + aarch64).

Основной проект собирается под Fedora 43/44/45/rawhide:

- Репозиторий: https://github.com/arcticlore/candy-rpm
- COPR: https://copr.fedorainfracloud.org/coprs/arcticlore/candy/

Здесь — только openSUSE.

## Статус

- COPR-проект: https://copr.fedorainfracloud.org/coprs/arcticlore/candy-opensuse/
- Пакетов: **127**
- Чруты: `opensuse-tumbleweed-x86_64`, `opensuse-tumbleweed-aarch64`,
  `opensuse-leap-16.0-x86_64`, `opensuse-leap-16.0-aarch64`
- Сборка: GitHub Actions → COPR. Каждый пакет сначала проходит **pilot**
  (`arcticlore/candy-opensuse-pilot`), и только при точном зелёном результате
  **4/4 chroot** продвигается в stable тем же исходным SRPM.

> ⚠️ Неофициальный сторонний репозиторий. Возможны редкие поломки на свежих
> релизах апстрима — пакет остаётся на предыдущей рабочей версии, заводите
> issue. Для каждого пакета штатный способ установки описан в `dnf info <pkg>`.

## Установка (openSUSE Tumbleweed / Leap 16.0)

```sh
sudo zypper addrepo --refresh \
  "https://copr.fedorainfracloud.org/coprs/arcticlore/candy-opensuse/repo/opensuse-tumbleweed-$basearch/arcticlore-candy-opensuse.repo" \
  arcticlore-candy-opensuse
sudo zypper --gpg-auto-import-keys refresh
sudo zypper install linuxwave   # или любой другой из 127 пакетов
```

Для Leap 16.0 замените `tumbleweed` в URL на `leap-16.0`.

### Миграция с боёвки (candy-opensuse-beta)

Боёвка `arcticlore/candy-opensuse-beta` **выведена из эксплуатации** и открыта
только на чтение. На stable уже перенесены все 127 проверенных пакетов beta:

```sh
sudo zypper removerepo arcticlore-candy-opensuse-beta
sudo zypper addrepo --refresh \
  "https://copr.fedorainfracloud.org/coprs/arcticlore/candy-opensuse/repo/opensuse-tumbleweed-$basearch/arcticlore-candy-opensuse.repo" \
  arcticlore-candy-opensuse
sudo zypper --gpg-auto-import-keys refresh
```

Старые пакеты боёвки обновятся на версии stable тем же `zypper update`
(пакеты перенесены на те же версии, что были записаны в beta).

## Как это устроено

- `pkgs.json` — единый список пакетов + openSUSE-чруты (тот же каталог, что
  в основном проекте). Каждая запись — пакет, который в stable либо уже
  зелёный 4/4, либо вплотную к этому на pilot.
- `bin/gen_specs.py` — генератор spec'ов под openSUSE:
  - определяет `%_licensedir` (в openSUSE его нет);
  - транслирует имена зависимостей Fedora → openSUSE
    (например `python3-devel` → `python311-devel`, `golang` → `go`,
    `cargo-rpm-macros` → `cargo-packaging`).
- `bin/auto-publish.py` + `.github/workflows/update.yml` — ежедневная волна:
  для каждой новой версии из апстрима собирается свежий SRPM, который идёт
  в pilot; при точном результате **4/4 succeeded** тот же SRPM продвигается
  в stable. Активные сборки не дублируются, при таймауте run перезапускается
  и продолжает наблюдение за существующими build id. Провал/отмена/пропуск/
  отсутствие чрута/ошибка API никогда не считаются успехом.
- `.github/workflows/validate.yml` и `drift-check` сверяют, что в stable и pilot
  нет расхождений по пакетному составу и что 4/4 достигнуто.

## Разделение контуров

| Проект | Роль |
|--------|------|
| `arcticlore/candy-opensuse` | **production stable**: 127 пакетов, сервируется юзерам |
| `arcticlore/candy-opensuse-pilot` | **staging**: свежие версии обкатываются на 4 chroot |
| `arcticlore/candy-opensuse-beta` | **retired**: старая боёвка, read-only, перенесена в stable |

## Лицензия

См. [LICENSE](LICENSE).
<br><br>

> Код предоставляется «как есть». Автор сам уже не помнит, как он работает. Удачи!