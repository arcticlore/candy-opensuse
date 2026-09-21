# Production approval packet — candy-opensuse-beta (ЧЕРНОВИК)

Статус: **DRAFT — производственный репозиторий НЕ одобрен**. Никаких
production-действий выполнять нельзя до отдельного решения владельца.

Сформирован: 2026-09-21 (UTC)
Base commit для возможного батча: `b6c25e0f4467ddd1916491febcb5b6f7b92bb0a5`
(short `b6c25e0`, «Merge pull request #13»).

## 1. Точный SHA базы

```
b6c25e0f4467ddd1916491febcb5b6f7b92bb0a5
```

`git log origin/master` (верхние 6):

```
b6c25e0 Merge pull request #13 from arcticlore/ci/bot-pr-required-checks
a3173b1 ci: bot-PR workflows — consistent PAT auth, strict fail-closed push/PR, gh in container, run-attempt branches
1b1cf14 ci: remove push-trigger probe artifact (item 11 evidence)
a605987 ci: push-trigger probe (item 11 evidence)
dd726c0 ci: remove [skip ci] from bot commits; prefer CANDY_BOT_TOKEN for bot PRs
365ed3d Merge pull request #11 from arcticlore/fix/termshot-gpkg
```

Исторический diff `730947a..365ed3d` затрагивает только `SPECS/termshot.spec` +
`pkgs.json` (поле gpkg) → current fingerprints остальных кандидатов
подтверждены проверкой `reports/opensuse-inventory.json`.

Новый diff `365ed3d..b6c25e0` (PR #13, bot-PR CI fix) затрагивает **только**:

- `.github/workflows/logrotate.yml`
- `.github/workflows/sync-specs.yml`
- `.github/workflows/update.yml`

и **не меняет** `SPECS/`, `pkgs.json`, `state/` или candidate fingerprints.
Следовательно fingerprints, подтверждённые базой `365ed3d`, остаются
валидными и на базе `b6c25e0`.

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
всех трёх prod-воркфлоу на master `b6c25e0` (diff `365ed3d..b6c25e0` меняет
только bot-PR-аутентификацию, см. item 11; `schedule:` не добавлялся).

## 6. Предлагаемый первый production batch — МАКСИМУМ 3

Все — current-fingerprint 4/4 (item 7). По риску/простоте:

1. `tealdeer` (Rust, cargo, чистый upstream release)
2. `glow` (Go, стабильная версия)
3. `colorls` (Ruby gem, gem_name валидирован в chroot-чеке)

Список финализируется перед отправкой и только с согласия владельца.

## 7. Только current-fingerprint 4/4

В батч включаются только пакеты с pilot 4/4 на коммитах, совпадающих с текущим
fingerprint. Исторический diff `730947a..365ed3d` — только termshot. Новый
diff `365ed3d..b6c25e0` (PR #13) меняет только три bot-PR workflow и не
затрагивает `SPECS/`/`pkgs.json`/`state/`, поэтому fingerprints, подтверждённые
базой `365ed3d`, остаются валидными и на базе `b6c25e0`. termshot включён
отдельным ретестом `11007766` после фикса gpkg. `maze` (расхождение версии,
п.2) в первый батч НЕ включать.

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

## 11. Bot-PR и required CI (item 11) — trigger/check matrix proven; PAT automation pending

Статус: **trigger/check-matrix доказана; PAT-медированная автоматизация —
pending** (секрет владельца + первый token-аутентифицированный bot-PR).

### PROVEN (обычная аутентификация push)

Валидированы оба триггера на branch `ci/bot-pr-required-checks`:

- `push` в `ci/**` → `validate` (push-событие) и `sync-specs`
- `pull_request` → `validate` (pull_request-событие)

Эксперимент (2026-09-21): push без `[skip ci]` в `ci/**` создал runs, все
success (финальный head PR #13 `a3173b1`):

- `validate` push 35581903187
- `sync specs` push 35581903093
- `validate` pull_request 35581908295

Принудительный `workflow_dispatch` validate на предыдущем ref (35576118660) → success.

Итог по PR #13 (head `a3173b1`): `gh pr checks 13` — все **6 required checks
PASS** (decommission, drift-check, tests, opensuse-specs ×2, waiters),
`mergeable=CLEAN`. PR #13 **смержен** (merge commit `b6c25e0`).

### Master post-merge validation (workflow_dispatch)

Автоматическая post-merge push-валидация master была **пропущена**: merge-сообщение
`b6c25e0` содержало нативный CI skip-маркер (строка `[skip ci]`, попавшая из
заголовка PR #13), поэтому GitHub API показывает **0 workflow runs** для этого
SHA. Master не переписывался (никаких revert/reset/force-push).

Вместо этого exact master был провалидирован вручную через `workflow_dispatch`:

- run **35584264340**, `event=workflow_dispatch`, `ref=master`,
  `head_sha=b6c25e0f4467ddd1916491febcb5b6f7b92bb0a5`;
- все шесть: decommission=success, drift-check=success, tests=success,
  opensuse-specs Tumbleweed=success, opensuse-specs Leap 16.0=success,
  waiters=success;
- ни один production workflow не был запущен (`update`/`submit`/`rebuild`/
  `logrotate` не диспатчились).

### NOT YET PROVEN (PAT-медированный путь)

Экспериментальный push-эр был `arcticlore`; `candy-bot` — только Git
author/committer. Секрет `CANDY_BOT_TOKEN` не присутствовал и не запускался.
Полный workflow-to-PAT путь (bot-воркфлоу использует PAT через
`secrets.CANDY_BOT_TOKEN`, push и `gh pr create` токен-аутентифицированы)
**не доказан** — ожидает секрета владельца и первого token-аутентифицированного
bot-PR.

### Fallback: GITHUB_TOKEN

События, создаваемые `GITHUB_TOKEN`, по правилам GitHub не порождают
рекурсивные workflow runs; они не гарантируют появление required checks в виде
approval-required — checks могут оставаться **absent/pending** (fail-closed).
Fallback fail-closed, но не fully automated.

### Фикс (PR #13)

- убраны все `[skip ci]` из bot-коммитов (главная причина zero-checks);
- `GH_TOKEN: ${{ secrets.CANDY_BOT_TOKEN || secrets.GITHUB_TOKEN }}` — при
  наличии repo-scoped PAT CI запускается без ручного approve;
- `git remote set-url origin https://x-access-token:${GH_TOKEN}@...` перед push.

**Вывод**: trigger/check-matrix доказана; полная автоматизация
PAT-медированного bot-PR заблокирована отсутствием секрета `CANDY_BOT_TOKEN`
(см. item 12.1).

## 12. Остающиеся блокеры и требуемые approvals

| # | блокер / требование | кем решается | статус |
|---|---------------------|--------------|--------|
| 1 | Секрет `CANDY_BOT_TOKEN` — repository-scoped Actions secret, минимальные Contents write + Pull requests write, желательно с expiration. Добавляет только владелец; PAT в чат/коммиты не выкладывать | владелец | НЕТ |
| 2 | `candy-production` environment approval | владелец | не одобрено |
| 3 | linuxwave: Zig toolchain (issue #12) | владелец + упаковщик | deferred |
| 4 | Одобрение первого production batch (≤3, item 6) | владелец | НЕТ |
| 5 | Согласование rollback-скрипта (item 9) | владелец | НЕТ |
| 6 | PR #13 (фикс bot-PR CI) — **смержен**; далее ревью PR #14 (DRAFT) | владелец | merged |

## Связанные артефакты

- inventory: `reports/opensuse-inventory.md`, `reports/opensuse-inventory-detailed.json`
- root causes: `reports/opensuse-root-causes.md`, `reports/opensuse-root-causes.json`
- blocker record: issue #12
- bot-PR CI фикс: PR #13 (merged → master `b6c25e0`)
- master post-merge validation: workflow_dispatch run `35584264340`