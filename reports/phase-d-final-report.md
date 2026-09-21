# Phase D: итоговый отчёт — pilot-валидация и подготовка production (DRAFT)

Репозиторий: arcticlore/candy-opensuse-beta
Сформирован: 2026-09-21 (UTC)
Статус: **DRAFT — production НЕ одобрен**. Итог этого отчёта — полнота
подготовительных материалов и доказательной базы для решения владельца.

## Результаты по когортам pilot

Проект-пилот: **candy-opensuse-pilot** (id `259423`), `enable_net=False`.
Chroots: `opensuse-tumbleweed-x86_64|aarch64`, `opensuse-leap-16.0-x86_64|aarch64`.

### Когорта 1, Run 35521344485 (8m35s) — 7 пакетов
| пакет | версия | build id | результат |
|------|--------|----------|-----------|
| colorls    | 1.5.0  | 11007577 | 4/4 succeeded |
| tealdeer   | 1.9.0  | 11007579 | 4/4 succeeded |
| glow       | 3.0.0  | 11007580 | 4/4 succeeded |
| jp2a       | 1.3.3  | 11007730 | 4/4 succeeded |
| archey4    | 4.15.0.0 | 11007731 | 4/4 succeeded |
| maze       | 20260919.eb99e65 | 11007738 | 4/4 succeeded |
| linuxwave  | 0.4.0  | 11007732 | **failed (Zig toolchain)** — см. issue #12 |

### Когорта 2 (termshot, фикс gpkg), Run 35525446670/35526997655
| пакет | версия | build id | результат |
|------|--------|----------|-----------|
| termshot  | 0.6.1 | 11007766 | 4/4 succeeded (ретест после фикса `gpkg` PR #11) |

### Когорта 3, Run 35526997655 (4m27s) — 4 пакета, все 4/4
| пакет | версия | build id | тип |
|------|--------|----------|-----|
| gtop          | 1.1.5 | 11007808 | Node (npm) |
| albafetch     | 4.3   | 11007809 | C (meson) |
| tty-solitaire | 1.4.1 | 11007810 | C (make) |
| neofetch      | 7.1.0 | 11007811 | bash (script) |

Итого подтверждённых candidates: **11 пакетов × 4/4** (все — current
fingerprint, база `b6c25e0`; diff `365ed3d..b6c25e0` меняет только три bot-PR
workflow и не затрагивает `SPECS/`/`pkgs.json`/`state/`/fingerprints).
Отвергнутые pilot-сборки: `11007734` termshot (старый gpkg), `11007732`
linuxwave (deferred).

## linuxwave — DEFERRED (решение владельца, OPTION 3)

Blocker record: issue #12 (https://github.com/arcticlore/candy-opensuse-beta/issues/12).
Leap 16.0 chroot: только `zig-0.12.0`; linuxwave `build.zig.zon` (v0.4.0) имеет
`.name = .linuxwave,` (пост-0.13 синтаксис, `minimum_zig_version = "0.16.0"`) →
`build.zig.zon:2:14: error: expected string literal`; zig-clap 0.12.0 требует
Zig >= 0.16.0-dev. В `openSUSE:Leap:16.0` zig0.16/0.15/0.14 отсутствуют.
Гипотетический offline-вендоринг покрывал бы только TW → максимум 2/4 → не
принято. Из production-батча исключён.

## Bot-PR и required CI (item 11) — trigger/check matrix proven; PAT automation pending

Статус: **trigger/check-matrix доказана; PAT-медированная автоматизация —
pending** (секрет владельца + первый token-аутентифицированный bot-PR).

**PROVEN** (обычная аутентификация push; push-эр `arcticlore`, `candy-bot` —
только author/committer):

- ветка `ci/bot-pr-required-checks` (PR #13: убран `[skip ci]`, добавлен
  `GH_TOKEN: ${{ secrets.CANDY_BOT_TOKEN || secrets.GITHUB_TOKEN }}`,
  remote set-url→токен перед push);
- свежий push без `[skip ci]` в `ci/**` (head `1b1cf14`): validate push
  35580139929 + sync-specs push 35580139956 + validate pull_request
  35580146732 — все success;
- принудительный `workflow_dispatch` validate 35576118660 — success;
- PR #13: `mergeable=CLEAN`, все **6 required checks pass** (decommission,
  drift-check, tests, opensuse-specs ×2, waiters); **смержен** → master
  `b6c25e0` (merge commit `b6c25e0f4467ddd1916491febcb5b6f7b92bb0a5`).

### Master post-merge validation

Автоматическая post-merge push-валидация master **пропущена**: merge-сообщение
`b6c25e0` содержало нативный CI skip-маркер (`[skip ci]` из заголовка PR #13),
поэтому для этого SHA — **0 workflow runs**. Master не переписывался. Exact
master провалидирован вручную через `workflow_dispatch`:

- run **35584264340**, `event=workflow_dispatch`, `head_sha=b6c25e0f4467ddd1916491febcb5b6f7b92bb0a5`;
- decommission=success, drift-check=success, tests=success, opensuse-specs
  Tumbleweed=success, opensuse-specs Leap 16.0=success, waiters=success;
- production workflow не диспатчился.

**NOT YET PROVEN**: end-to-end workflow с `CANDY_BOT_TOKEN` (секрет не
присутствовал и не использовался) — ждёт секрета владельца и первого
token-аутентифицированного bot-PR.

**Fallback**: события от `GITHUB_TOKEN` не порождают рекурсивные runs; required
checks могут оставаться absent/pending — fail-closed, не fully automated.

Объяснение прежнего zero-checks: `[skip ci]` в bot-коммитах (главная причина) +
правило GitHub о событиях от `GITHUB_TOKEN`.

## Артефакты

| файл | содержание |
|------|------------|
| `reports/opensuse-baseline*.{md,json}` | исходный бенчмарк |
| `reports/opensuse-inventory*.{md,json}` | inventory 128 пакетов, пер-chroot |
| `reports/opensuse-root-causes*.{md,json}` | root causes (build-phase, builddep, rpmbuild) |
| `reports/production-approval-packet.md` | **12-item approval packet (DRAFT)** |
| issue #12 | blocker record: linuxwave DEFERRED |
| PR #13 | фикс bot-PR CI (6/6 required checks); merged → master `b6c25e0` |
| run `35584264340` | ручная post-merge валидация master `b6c25e0` (workflow_dispatch) |
| PR #14 | коммит approval packet (DRAFT) |

## Остающиеся блокеры (item 12)

1. Секрет `CANDY_BOT_TOKEN` — repository-scoped Actions secret, минимальные
   Contents write + Pull requests write, желательно с expiration. Добавляет
   только владелец; PAT в чат/коммиты не выкладывать.
2. `candy-production` environment approval — владелец.
3. linuxwave Zig toolchain (issue #12) — deferred.
4. Одобрение первого production batch (≤3) — владелец.
5. Согласование rollback-скрипта — владелец.
6. PR #13 (фикс bot-PR CI) — **merged**; далее ревью PR #14 (DRAFT).

## Запрещено до отдельного решения владельца

Production submit/rebuild/full-rebuild/environment approval/`enable_net=True`/
новые linuxwave builds.