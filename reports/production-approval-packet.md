# Production approval packet — candy-opensuse-beta (ЧЕРНОВИК)

Статус: **DRAFT — производственный репозиторий НЕ одобрен**. Никаких
production-действий выполнять нельзя до отдельного решения владельца.

Сформирован: 2026-09-21 (UTC)
Base commit для возможного батча: `365ed3ddb811268e5bb44d0d612504ac1068004f`
(short `365ed3d`, «Merge pull request #11»).

## 1. Точный SHA базы

```
365ed3ddb811268e5bb44d0d612504ac1068004f
```

`git log origin/master` (верхние 5):

```
365ed3d Merge pull request #11 from arcticlore/fix/termshot-gpkg
be9a21e fix(termshot): point gpkg at ./cmd/termshot (main package)
730947a Merge pull request #10 from arcticlore/containment/pause-production-submit
d0e116a containment: pause automatic production submit
b0afcfb Merge pull request #9 from arcticlore/phase-d/reports-commit
```

Diff `730947a..365ed3d` затрагивает только `SPECS/termshot.spec` + `pkgs.json`
(поле gpkg) → current fingerprints остальных кандидатов подтверждены проверкой
`reports/opensuse-inventory.json` от этой же базы.

## 2. Таблица кандидатов (уникальные пакеты, pilot 4/4, current fingerprint)

Все сборки выполнены в **candy-opensuse-pilot** (id `259423`), каждый на 4
chroots: `opensuse-tumbleweed-x86_64`, `opensuse-tumbleweed-aarch64`,
`opensuse-leap-16.0-x86_64`, `opensuse-leap-16.0-aarch64` — **все 4 succeeded**.

| пакет        | версия (state.json) | build id (pilot) | тип/система                  |
|--------------|---------------------|------------------|------------------------------|
| colorls      | 1.5.0               | 11007577         | Ruby gem                     |
| tealdeer     | 1.9.0               | 11007579         | Rust (cargo)                 |
| glow         | 3.0.0               | 11007580         | Go                           |
| jp2a         | 1.3.3               | 11007730         | C (imagemagick)              |
| archey4      | 4.15.0.0            | 11007731         | Python                       |
| maze         | 20260919.eb99e65   | 11007738         | Rust (cargo)                 |
| termshot     | 0.6.1               | 11007766         | Go — **ретест после фикса gpkg** |
| gtop         | 1.1.5               | 11007808         | Node (npm)                   |
| albafetch    | 4.3                 | 11007809         | C (meson)                    |
| tty-solitaire| 1.4.1               | 11007810         | C (make)                     |
| neofetch     | 7.1.0               | 11007811         | bash (script)                |

Примечание: версия собранного `maze` (в rpm) — `20260920.eb99e65-1`, в
`state.json` — `20260919.eb99e65`; расхождение — одна коммита метки сборки.
maze в первый батч не входит (см. item 7); синхронизация версии — отдельное
требование.

Проверено (COPR API `build-chroot/list?build_id=<id>&limit=100`): у каждого из
11 build id все 4 chroot в состоянии `succeeded`.

## 3. COPR build id + 4 terminal chroot states

| build id | chroots | terminal states (все 4) |
|----------|---------|--------------------------|
| 11007577 | 4 | succeeded × 4 (colorls)    |
| 11007579 | 4 | succeeded × 4 (tealdeer)   |
| 11007580 | 4 | succeeded × 4 (glow)       |
| 11007730 | 4 | succeeded × 4 (jp2a)       |
| 11007731 | 4 | succeeded × 4 (archey4)    |
| 11007738 | 4 | succeeded × 4 (maze)       |
| 11007766 | 4 | succeeded × 4 (termshot, ретест) |
| 11007808 | 4 | succeeded × 4 (gtop)       |
| 11007809 | 4 | succeeded × 4 (albafetch)  |
| 11007810 | 4 | succeeded × 4 (tty-solitaire) |
| 11007811 | 4 | succeeded × 4 (neofetch)   |

Отвергнутые pilot-сборки (не кандидаты): `11007732` linuxwave (failed — item 8),
`11007734` termshot (failed, старый gpkg).

## 4. Production project config и enable_net state

Проект: **candy-opensuse-beta**, id `258783`, owner `arcticlore`.

- `enable_net = False` (изолированная сборка, без сетевого доступа — сохранено)
- `auto_rebuild = None`
- chroots: `opensuse-tumbleweed-x86_64|aarch64`, `opensuse-leap-16.0-x86_64|aarch64`
- additional_repos: пусто

Пилот `candy-opensuse-pilot`, id `259423`: тоже `enable_net=False`, те же
chroots. Production не имеет автоматического сетевого доступа.

## 5. Нет автоматического production schedule

- `update.yml`: только `workflow_dispatch`, **без `schedule:`** (containment, PR #10).
- `sync-specs.yml`: `workflow_dispatch` + `push` на `ci/**` (не в master).
- `logrotate.yml`: только `workflow_dispatch`.

Автоматического production submit не существует — подтверждено просмотром
всех трёх prod-воркфлоу на master `365ed3d`.

## 6. Предлагаемый первый production batch — МАКСИМУМ 3

Все — current-fingerprint 4/4 (item 7). По риску/простоте:

1. `tealdeer` (Rust, cargo, чистый upstream release)
2. `glow` (Go, стабильная версия)
3. `colorls` (Ruby gem, gem_name валидирован в chroot-чеке)

Список финализируется перед отправкой и только с согласия владельца.

## 7. Только current-fingerprint 4/4

В батч включаются только пакеты с pilot 4/4 на коммитах, совпадающих с текущим
fingerprint. Diff `730947a..365ed3d` — только termshot; fingerprints остальных
подтверждены базой `365ed3d`. termshot включён отдельным ретестом `11007766`
после фикса gpkg. `maze` (расхождение версии, п.2) в первый батч НЕ включать.

## 8. linuxwave — DEFERRED, не входит в батч

- Issue #12: "linuxwave: DEFERRED — Zig toolchain blocker on Leap 16.0 (needs Zig >= 0.16)"
  — https://github.com/arcticlore/candy-opensuse-beta/issues/12
- Причина: Leap 16.0 chroot имеет только `zig-0.12.0` (`zig-0.12.0-bp160.1.9`);
  `build.zig.zon` у linuxwave v0.4.0 использует `.name = .linuxwave,` (пост-0.13
  синтаксис) → `build.zig.zon:2:14: error: expected string literal`;
  `minimum_zig_version = "0.16.0"`; zig-clap 0.12.0 требует
  `0.16.0-dev.2261+d6b3dd25a`. В `openSUSE:Leap:16.0` нет zig0.16/0.15/0.14.
  Offline-вендоринг покрывает только TW → максимум 2/4 → НЕ принят.
- В pilot/inventory не менялся; из production-батча исключён.

## 9. Stop-next-batch и rollback plan

- **Stop**: следующий батч запускается только через `workflow_dispatch`
  (владелец не запускает — батч не идёт). Перед батчем проверять активные
  builds (сейчас 0 — item 10).
- **Rollback**: пакет в COPR public storage приходит из последнего успешного
  build-chroot проекта `candy-opensuse-beta`. Откат конкретного пакета:
  1) пересобрать предыдущий успешный build id (из pilot-истории п.3) в beta;
  2) либо удалить/перепометить владельцем через COPR API (ручное admin-
     действие в copr-cli — не автоматизируется).
  Упаковка rollback-скрипта (`bin/rollback-*.py`) — TODO, согласовывается с
  владельцем до первого батча.

## 10. Проверка отсутствия активных builds

COPR API `build/list?ownername=arcticlore&projectname=...&limit=120`:

- candy-opensuse-beta: 120 fetched, активных **0**
- candy-opensuse-pilot: 36 fetched, активных **0**

Активными считаются состояния вне `{succeeded,failed,skipped,canceled}`.

## 11. Bot-PR получает required CI (item 11) — ДОКАЗАНО

Валидированы оба триггера на branch `ci/bot-pr-required-checks`:

- `push` в `ci/**` → `validate` (push-событие) и `sync-specs`
- `pull_request` → `validate` (pull_request-событие)

Эксперимент (2026-09-21): push `a605987` на `ci/bot-pr-required-checks` создал
3 runs, все **success**:

- `validate` push 35576274203
- `sync specs` push 35576274211
- `validate` pull_request 35576278380

Принудительный `workflow_dispatch` validate на том же ref (35576118660) → success.

Итог по PR #13: `gh pr checks 13` — все **6 required checks PASS**
(decommission, drift-check, tests, opensuse-specs ×2, waiters),
`mergeable=CLEAN`.

Объяснение прежнего «zero checks»: bot-коммиты делались с префиксом `[skip ci]`,
который полностью подавляет запуск; события, создаваемые `GITHUB_TOKEN`, по
правилам GitHub не запускают новые workflow runs (исключения —
`workflow_dispatch`/`repository_dispatch`/`pull_request` в состоянии
approval-required). Фикс (PR #13):

- убраны все `[skip ci]` из bot-коммитов;
- `GH_TOKEN: ${{ secrets.CANDY_BOT_TOKEN || secrets.GITHUB_TOKEN }}` — при
  наличии fine-grained PAT CI запускается без ручного approve;
- `git remote set-url origin https://x-access-token:${GH_TOKEN}@...` перед push.

Вывод: для полностью автоматического CI на bot-PR остаётся единственный
заблокированный элемент — секрет `CANDY_BOT_TOKEN` (добавляет владелец; без
него bot-PR работает в режиме approval-required).

## 12. Остающиеся блокеры и требуемые approvals

| # | блокер / требование | кем решается | статус |
|---|---------------------|--------------|--------|
| 1 | Секрет `CANDY_BOT_TOKEN` (fine-grained PAT: contents+pull-requests write) | владелец | НЕТ |
| 2 | `candy-production` environment approval | владелец | не одобрено |
| 3 | linuxwave: Zig toolchain (issue #12) | владелец + упаковщик | deferred |
| 4 | Одобрение первого production batch (≤3, item 6) | владелец | НЕТ |
| 5 | Согласование rollback-скрипта (item 9) | владелец | НЕТ |
| 6 | Reviewer-approval PR #13 (фикс bot-PR CI) → merge | владелец | открыт |

## Связанные артефакты

- inventory: `reports/opensuse-inventory.md`, `reports/opensuse-inventory-detailed.json`
- root causes: `reports/opensuse-root-causes.md`, `reports/opensuse-root-causes.json`
- blocker record: issue #12
- bot-PR CI фикс: PR #13