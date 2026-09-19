# Phase A — baseline (2026-09-19)

Ветка: `fix/opensuse-pipeline`. Статус до ремонта зафиксирован.

## Остановлен schedule
`AZ5` — файл-кар: commit `1e4b4c3` (при push). Scheduled submit (`update.yml`,
cron `43 3 * * *`) отключён. Снятие — только после полного зелёного ребилда.

## Найденные проблемы (подтверждено фактами)

### 1. COPR-проект использует 4 chroot (должно быть 2)
Всё сабмит-задания последних волн содержат:
`opensuse-tumbleweed-x86_64`, `opensuse-tumbleweed-aarch64`,
**`opensuse-leap-16.0-x86_64`, `opensuse-leap-16.0-aarch64`**.

- 273 билда из 273 имеют оба Tumbleweed; 143 билда дополнительно Leap.
- README утверждает «только Tumbleweed» — фактически Leap участвует.

### 2. Ни один генератор не идемпотентен по `--all`
`python3 bin/gen_specs.py --all` первым прогоном меняет **73 из 128** спеков:

- причина: закоммиченные `SPECS/*.spec` содержат фактический topdir из
  `CANDY_TOPDIR` окружения, которое выставляет `bin/make-srpm.sh` после
  распаковки (`%autosetup -n broot-1.60.1`, `-n ascsaver-<sha>` и т.п.);
- повторный `--all` без env генерит макрос `%{name}-%{version}`/`%{name}-v%{version}`
  → спеки меняются; повторный второй прогон даёт 0 диффов (двукратная фиксация).
- 117/128 спеков требуют фактического topdir (`fact != macro`), т.к. реальный
  корень тарбола часто не совпадает с `%{name}-%{version}` (дуальное имя crates,
  sha-коммиты, префиксы `v`, `gping-gping-v%{version}` и др.).

→ Фикс в фазе C: детерминированный topdir (поле в pkgs.json + нормализация
тарбола в make-srpm.sh) чтобы `git diff --exit-code` был зелёным.

### 3. `bin/gen-pages-html.py` отсутствовал → тесты исключались через `-k`
Тесты `test_gen_pages` и `test_edge_cases` импортируют `bin/gen-pages-html.py`,
которого в бете не было → CI прятал их `-k "not GenPages"`.
Скрипт восстановлен и адаптирован (проект `candy-opensuse-beta`, полная
пагинация, `CANDY_COPR_PROJECT` env).

Все 112 тестов проходят теперь **без** исключений:
`python3 -m pytest tests/ -q` → `112 passed`.

### 4. Baseline COPR (опять роспуск Tumbleweed)
`reports/opensuse-baseline.json` + `.md` (генерирует `bin/copr-baseline.py`):

| метрика | значение |
|---|---|
| enabled в pkgs.json | 128 |
| есть билды в COPR | 123 |
| missing (enabled, нет билдов) | 5 → fend, pure, tabiew, wtfutil, zenith |
| последний билд succeeded | 54 |
| последний билд failed | 69 |
| хоть раз succeeded когда-либо | 57 |
| никогда не собирался зелёным | 71 |

Причины фейлов по последним билдам (из builder-live.log.gz, оба Tumbleweed):
- `build-phase-error`: **65** (главным образом `%cargo_prep`/`%cargo_build`/
  `%cargo_install` — макросы Fedora отсутствуют в openSUSE; `%pyproject_buildrequires`)
- `builddep-unresolved`: **4** (Rio→`glslang`, archey4→`python3-dbus`,
  cyme→`libusb1-devel`, jp2a→`libjpeg-turbo-devel`)

Подтверждено в логах: у `broot` — `+ %cargo_prep` → `Bad exit status (%prep)`;
у `Rio` — `No match for argument: glslang` на стадии `dnf5 builddep`.

### 5. «verify» и Supply chain (наблюдения, детализация в фазе E)
- Specs: `Source0: %{name}-%{version}.tar.gz`, нет checksum/SHA256
  (`verify` поле не используется в пайплайне нигде).
- make-srpm.sh: `curl ... -C -` без сверки контрольной суммы; кеширует
  `SOURCES/$NAME-$VER.tar.gz` без проверки.
- Инъекция версии: NO-OP.

## Что не трогалось в фазе A (следующ. фазы)
- `%cargo_prep`/`%cargo_build`/`%cargo_install` (56 спека), `%pyproject_buildrequires` (8).
- Неверные BR (`glslang`, `python3-dbus`, `libusb1-devel`, `libjpeg-turbo-devel`, ...).
- auto-issue.json (awk), validate.yml (`-k not GenPages`, `|| true`).
- Leap-чруты в конфиге COPR (нужны права owner).

## Команды воспроизводства
```
python3 bin/copr-baseline.py          # переснять baseline
python3 -m pytest tests/ -q           # 112 passed (без -k)
python3 bin/gen_specs.py --all        # неидемпотентно (73 спека) — фаза C
```