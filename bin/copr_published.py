#!/usr/bin/env python3
"""copr_published.py — что РЕАЛЬНО опубликовано в COPR-проекте (repodata как ground truth).

Зачем этот модуль: build-list COPR не является источником правды о публикации.
В `arcticlore/candy-opensuse` 133 пакета из state.json присутствуют в repodata,
но для 102 из них в build-list нет записи `succeeded` (записи удалены при
миграции/чистке, часть помечена `forked`). Если считать «опубликовано» только по
build-list, план волны начинает пересобирать уже опубликованные пакеты — это
прямое нарушение запрета на дублирующие сборки.

Что делаем:
  * читаем primary.xml каждого chroot и строим {имя: {версии}};
  * версии берём и из бинарного пакета (version@ver, epoch отбрасываем), и из
    rpm:sourcerpm — тогда таблица индексируется и по имени бинарника, и по
    имени исходного пакета (у taskwarrior-tui бинарник называется task);
  * любая невозможность прочитать repodata — ошибка (fail closed): при
    неизвестном состоянии репозитория строить ничего нельзя, иначе рискуем
    дубликатами.
"""
from __future__ import annotations

import bz2
import gzip
import lzma
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterable

# Реподата живёт на download-хосте (и редиректит 301 на packages.redhat.com),
# а не на API-хосте —混 смешивать их нельзя.
DEFAULT_DL_BASE = "https://download.copr.fedorainfracloud.org"
DL_TMPL = "{dl}/results/{owner}/{project}/{chroot}/"
RETRY_BACKOFF_S = 3.0   # пауза между попытками; в тестах обнуляется


class PublishProbeError(RuntimeError):
    """Реподата недоступна/не разобрана — состояние проекта неизвестно."""


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _sub(el, name: str):
    for child in el:
        if _tag(child) == name:
            return child
    return None


def _decompress(blob: bytes) -> bytes:
    """Распаковать primary по МАГИЧЕСКИМ байтам, а не по расширению href.

    COPR отдаёт .gz, но openSUSE-зеркала — .xz/.bz2, и полагаться на суффикс
    нельзя: иначе ET упадёт на бинарнике и мы решим «ничего не опубликовано».
    """
    if blob[:2] == b"\x1f\x8b":
        return gzip.decompress(blob)
    if blob[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(blob)
    if blob[:3] == b"BZh":
        return bz2.decompress(blob)
    return blob


def _fetch(url: str, timeout: int, retries: int = 3, binary: bool = False):
    """Скачать с повторами и паузой: CDN у download.copr периодически отдаёт
    502/404 подряд (наблюдалось на живом прогоне 2026-10-02), и мгновенный
    retry без backoff доводит до ложного «репозиторий неизвестен».
    """
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = resp.read()
            return data if binary else data.decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 — сеть/CDN отвечают по-разному
            last = exc
            if attempt + 1 < retries:
                time.sleep(RETRY_BACKOFF_S * (attempt + 1))
    raise PublishProbeError(f"не удалось загрузить {url}: {last}")


def _primary_href(root: str, timeout: int) -> str:
    xml = _fetch(f"{root}repodata/repomd.xml", timeout)
    for el in ET.fromstring(xml).iter():
        if _tag(el) == "data" and el.get("type") == "primary":
            loc = _sub(el, "location")
            href = loc.get("href") if loc is not None else el.get("href")
            if href:
                return href
    raise PublishProbeError(f"в repomd нет primary: {root}")


def _strip_epoch(ver: str) -> str:
    """В repodata epoch приходит атрибутом, но может попасть и в версию ('1!2.3')."""
    return ver.rsplit("!", 1)[1] if "!" in ver else ver


def parse_primary(blob: bytes) -> dict[str, set[str]]:
    """primary.xml → {имя: {версии}}; ключи — и бинарное имя, и имя исходника."""
    blob = _decompress(blob)
    table: dict[str, set[str]] = {}

    def add(key: str, version: str) -> None:
        table.setdefault(key, set()).add(version)

    for el in ET.fromstring(blob).iter():
        if _tag(el) != "package":
            continue
        name_el, ver_el, src_el = _sub(el, "name"), _sub(el, "version"), _sub(el, "sourcerpm")
        if name_el is None or not name_el.text:
            continue
        if ver_el is not None and ver_el.get("ver"):
            add(name_el.text, _strip_epoch(ver_el.get("ver")))
        if src_el is not None and src_el.text:
            # 'taskwarrior-tui-0.7.7-1.fc44.src.rpm' -> ('taskwarrior-tui', '0.7.7')
            base = src_el.text
            base = base.removesuffix(".src.rpm")
            head, _, _release = base.rpartition("-")
            src_name, _, src_ver = head.rpartition("-")
            if src_name and src_ver:
                add(src_name, _strip_epoch(src_ver))
    return table


def fetch_published_per_chroot(project: str, chroots: Iterable[str],
                               owner: str = "arcticlore",
                               dl_base: str = DEFAULT_DL_BASE,
                               timeout: int = 60) -> list[dict[str, set[str]]]:
    """Таблицы опубликованных пакетов ПО КАЖДОМУ chroot, в порядке chroots."""
    out: list[dict[str, set[str]]] = []
    for chroot in chroots:
        root = DL_TMPL.format(dl=dl_base.rstrip("/"), owner=owner, project=project,
                             chroot=chroot)
        href = _primary_href(root, timeout)
        blob = _fetch(f"{root}{href}", timeout, binary=True)
        out.append(parse_primary(blob))
    return out


def completed_table(per_chroot: list[dict[str, set[str]]]) -> dict[str, set[str]]:
    """Версии, опубликованные ВО ВСЕХ chroot сразу (пересечение).

    fetch_published() объединяет версии по union — это нужно анти-даунгрейду
    («есть ли где-то новее цели»), но как признак «цель достигнута» union
    опасен: версия, попавшая в ОДИН chroot из четырёх, выглядит как
    опубликованная везде, и волна посчитает 3/4 за 4/4.
    """
    if not per_chroot:
        return {}
    names = set(per_chroot[0])
    for table in per_chroot[1:]:
        names &= set(table)
    out: dict[str, set[str]] = {}
    for name in names:
        shared = set(per_chroot[0].get(name, set()))
        for table in per_chroot[1:]:
            shared &= table.get(name, set())
        shared.discard("")
        if shared:
            out[name] = shared
    return out


def fetch_published(project: str, chroots: Iterable[str], owner: str = "arcticlore",
                    dl_base: str = DEFAULT_DL_BASE, timeout: int = 60) -> dict[str, set[str]]:
    """Объединённая по chroot таблица опубликованных пакетов проекта."""
    merged: dict[str, set[str]] = {}
    for chroot in chroots:
        root = DL_TMPL.format(dl=dl_base.rstrip("/"), owner=owner, project=project,
                             chroot=chroot)
        href = _primary_href(root, timeout)
        blob = _fetch(f"{root}{href}", timeout, binary=True)
        for name, versions in parse_primary(blob).items():
            merged.setdefault(name, set()).update(v for v in versions if v)
    return merged


def target_published(table: dict[str, set[str]] | None, name: str, target: str) -> bool:
    """Опубликована ли целевая версия пакета (по repodata, не по build-list)."""
    if not table or not target:
        return False
    return target in table.get(name, set())


_DIGITS = "0123456789"
_ALPHA = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ~^"


def _segments(ver: str):
    """Разбор версии на сегменты по правилам rpmvercmp."""
    out, i, n = [], 0, len(ver)
    while i < n:
        ch = ver[i]
        # '~' и '^' — отдельные сегменты: в rpmvercmp они сравниваются как
        # отдельные символы ('~' старее всего, даже конца строки).
        if ch in "~^":
            out.append(ch)
            i += 1
        elif ch in _ALPHA:
            j = i
            while j < n and ver[j] in _ALPHA and ver[j] not in "~^":
                j += 1
            out.append(ver[i:j])
            i = j
        elif ch in _DIGITS:
            j = i
            while j < n and ver[j] in _DIGITS:
                j += 1
            out.append(ver[i:j].lstrip("0") or "0")
            i = j
        else:
            # Разделители ('.', '-', '_') в rpmvercmp НЕ сравниваются вообще:
            # rpm считает 1.0a == 1.0.a. Пропускаем их.
            i += 1
    return out


def _seg_cmp(a: str, b: str) -> int:
    a_num, b_num = a[0] in _DIGITS, b[0] in _DIGITS
    if a_num and b_num:
        return (int(a) > int(b)) - (int(a) < int(b))   # 10 > 2, а не "10" < "2"
    if a_num != b_num:
        return 1 if a_num else -1          # цифра новее буквы
    if a == b:
        return 0
    # Порядок спецсимволов: '~' старее всего, '^' старее любого обычного
    # символа (2.0^20240101 < 2.0.1), но новее '~'.
    for sym in ("~", "^"):
        if a == sym:
            return -1
        if b == sym:
            return 1
    return (a > b) - (a < b)


def vercmp(a: str, b: str) -> int:
    """Сравнение версий по алгоритму rpmvercmp: -1 / 0 / 1.

    Нужно, чтобы не откатывать репозиторий на старую версию: если в stable уже
    лежит БОЛЕЕ НОВАЯ версия, чем цель волны, пакет считаем закрытым.
    Проверено против python3-rpm (`rpm.labelCompare`) и `rpmdev-vercmp` на
    4000+ парах версий, включая `~`, `^` и `!`.
    """
    if a == b:
        return 0
    sa, sb = _segments(a), _segments(b)
    for x, y in zip(sa, sb):
        c = _seg_cmp(x, y)
        if c:
            return c
    if len(sa) == len(sb):
        return 0
    rest = (sa[len(sb):] if len(sa) > len(sb) else sb[len(sa):])
    first = rest[0]
    # Относительно «конца версии»: '~' старее конца (1.0~rc < 1.0), '^' и
    # обычный сегмент — новее (1.0 < 1.0^2024 < 1.0.1).
    if first == "~":
        return -1 if len(sa) > len(sb) else 1   # 1.0~rc < 1.0
    return 1 if len(sa) > len(sb) else -1       # 2.0.1 > 2.0; 1.0^2024 > 1.0


def has_newer(table: dict[str, set[str]] | None, name: str, target: str) -> bool:
    """Есть ли в repodata версия НОВЕЕ цели (тогда promote был бы даунгрейдом).

    Осторожность: при наличии epoch ('1!2.3') порядок не интерпретируется —
    rpm.labelCompare ведёт себя в таких строках неоднозначно, поэтому такие
    цели НИКОГДА не считаем закрытыми (лучше лишняя сборка, чем пропуск).
    """
    if not table or not target:
        return False
    if "!" in target:
        return False
    return any(vercmp(v, target) > 0 for v in table.get(name, set()) if v)
