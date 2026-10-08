#!/usr/bin/env python3
"""preflight.py — fail-closed mirror preflight для openSUSE-волны публикации.

Перед ЛЮБЫМ submit волна обязана доказать, что зеркало COPR для openSUSE
живо и внутренне согласовано: repository metadata (repomd + primary) реально
скачивается, zypper refresh проходит, а содержимое скачанного пакета
байт-в-байт совпадает с checksum из metadata. Любая HTTP/transport/checksum/
metadata/zypper-ошибка — это RED: submit'ы запрещены (fail-closed).

Скрипт написан так, чтобы его можно было запускать в контейнере Tumbleweed
(нативный zypper) И изолированно тестировать: сеть и подпроцессы вынесены за
абстракции ``fetcher`` и ``runner``, а решение принимается по чистым функциям.

Exit codes:
  0 — preflight зелёный (submit можно);
  1 — preflight красный (submit НЕЛЬЗЯ);
  2 — ошибка использования.

Использование:
    python3 bin/preflight.py \
        --repo-url https://download.copr.fedorainfracloud.org/results/arcticlore/candy-opensuse/opensuse-tumbleweed-x86_64 \
        --alias arcticlore-candy-opensuse \
        --chroot opensuse-tumbleweed-x86_64 \
        --package xh \
        --out logs/preflight.json --log-dir logs
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_NS = {"r": "http://linux.duke.edu/metadata/repo"}
COMMON_NS = {"c": "http://linux.duke.edu/metadata/common"}


class PreflightError(Exception):
    """Ошибка, которую нельзя пропустить: preflight обязан быть красным."""


# ---------------------------------------------------------------------------
# Чистые части: их проверяют unit-тесты без сети и без zypper.
# ---------------------------------------------------------------------------
def classify(reason: str) -> str:
    """Категория для диагностики: http|transport|checksum|metadata|zypper|content."""
    r = reason.lower()
    for tag in ("checksum", "metadata", "zypper", "content", "http", "transport"):
        if tag in r:
            return tag
    return "other"


def parse_repomd(repomd_xml: bytes) -> dict:
    """repomd.xml -> сведения о primary-данных (href, checksum).

    Preferимущество отдаётся обычному (gzip/xml) primary: zchunk-only metadata
    мы не умеем читать без libzck, и это должно быть видно как отдельная
    причина, а не молчаливый пропуск проверки.
    """
    try:
        root = ET.fromstring(repomd_xml)
    except ET.ParseError as exc:
        raise PreflightError(f"metadata: repomd.xml не разбирается: {exc}") from exc
    candidates: list[dict] = []
    for data in root.findall("r:data", REPO_NS):
        dtype = data.get("type")
        if dtype not in ("primary", "primary_db"):
            continue
        loc = data.find("r:location", REPO_NS)
        cks = data.find("r:checksum", REPO_NS)
        if loc is None or not loc.get("href"):
            continue
        href = loc.get("href")
        candidates.append({
            "type": dtype,
            "href": href,
            "checksum": (cks.text.strip() if cks is not None and cks.text else None),
            "checksum_type": (cks.get("type") if cks is not None else None),
        })
    if not candidates:
        raise PreflightError("metadata: в repomd.xml нет primary-данных")
    gz = [c for c in candidates if c["href"].endswith((".xml.gz", ".xml"))]
    if gz:
        return gz[0]
    return candidates[0]


def decompress_metadata(raw: bytes, href: str) -> bytes:
    """primary обычно .xml.gz; plain .xml отдаём как есть, zck — ошибка."""
    if href.endswith(".zck"):
        raise PreflightError(
            f"metadata: primary {href} в zchunk-формате — не читается без libzck")
    if href.endswith(".gz") or raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except OSError as exc:
            raise PreflightError(f"metadata: primary gzip битый ({href}): {exc}") from exc
    return raw


def parse_primary(primary_xml: bytes, name: str,
                  wanted_version: str | None = None) -> dict:
    """Найти БИНАРНЫЙ пакет в primary.xml -> {version, arch, location, ...}.

    Source-rpm (arch src/nosrc) пропускаются: он присутствует в COPR-метаданных,
    но zypper скачивает бинарник, и сравнение его sha256 с checksum src.rpm дало
    бы ложный «checksum mismatch». Нам нужен именно тот артефакт, что скачается.
    """
    try:
        root = ET.fromstring(primary_xml)
    except ET.ParseError as exc:
        raise PreflightError(f"metadata: primary не разбирается: {exc}") from exc
    seen: list[str] = []
    src_archs = {"src", "nosrc"}
    for pkg in root.findall("c:package", COMMON_NS):
        n_el = pkg.find("c:name", COMMON_NS)
        if n_el is None or n_el.text != name:
            continue
        arch_el = pkg.find("c:arch", COMMON_NS)
        arch = arch_el.text if arch_el is not None else None
        if (arch or "").lower() in src_archs:
            continue
        ver_el = pkg.find("c:version", COMMON_NS)
        ver = ver_el.get("ver") if ver_el is not None else None
        seen.append(ver or "?")
        if wanted_version and ver != wanted_version:
            continue
        loc = pkg.find("c:location", COMMON_NS)
        cks = pkg.find("c:checksum", COMMON_NS)
        return {
            "version": ver,
            "arch": arch,
            "location": (loc.get("href") if loc is not None else None),
            "checksum": (cks.text.strip() if cks is not None and cks.text else None),
            "checksum_type": (cks.get("type") if cks is not None else None),
        }
    if seen:
        raise PreflightError(
            f"metadata: {name} есть в primary, но версии {wanted_version!r} нет "
            f"(доступны: {sorted(set(seen))})")
    raise PreflightError(f"metadata: пакет {name} отсутствует в primary")


def copr_pubkey_url(repo_url: str) -> str | None:
    """URL GPG-ключа COPR-проекта: лежит рядом с chroot-каталогом.

    Без этого ключа zypper валит refresh на «Signature verification failed»
    для repomd.xml — это не сломанное зеркало, а неимпортированный ключ.
    """
    base = repo_url.rstrip("/")
    if "download.copr." not in base:
        return None
    parent = base.rsplit("/", 1)[0]
    return f"{parent}/pubkey.gpg"


def parse_rpm_nv(runner, rpm_path: str) -> tuple[str, str]:
    """NAME, VERSION скачанного rpm через `rpm -qp` (content-проверка)."""
    rc, out, err = runner.run(
        ["rpm", "-qp", "--qf", "%{NAME}\n%{VERSION}\n", rpm_path], timeout=120)
    if rc != 0:
        raise PreflightError(f"content: rpm -qp {rpm_path} rc={rc}: {err.strip()[:200]}")
    parts = [p for p in (out or "").splitlines() if p.strip()]
    if len(parts) < 2:
        raise PreflightError(f"content: rpm -qp вернул неожиданное: {out!r}")
    return parts[0].strip(), parts[1].strip()


def verify_rpm_digest(runner, rpm_path: str) -> None:
    """`rpm -K` — внутренняя целостность пакета; ошибка digest => red."""
    rc, out, err = runner.run(["rpm", "-K", "--nosignature", rpm_path], timeout=300)
    text = f"{out}\n{err}".lower()
    if rc != 0 or "digest" in text and "ok" not in text:
        raise PreflightError(
            f"checksum: rpm -K {os.path.basename(rpm_path)} rc={rc}: {text.strip()[:200]}")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(repomd_xml: bytes, primary_checksum: str | None) -> str:
    """Стабильная подпись сломанного зеркала: одинаковая у одного и того же сбоя.

    Нужна, чтобы повторный внешний mirror-failure распознавался как ТОТ ЖЕ, и
    включал/сохранял brake, а не порождал build storm.
    """
    h = hashlib.sha256()
    h.update(repomd_xml or b"")
    h.update(b"\x00")
    h.update((primary_checksum or "").encode())
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Абстракции сети и подпроцессов (в тестах подменяются).
# ---------------------------------------------------------------------------
class SubprocessRunner:
    def run(self, cmd, timeout=600):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout or "", r.stderr or ""


def urllib_fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "candy-preflight/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if getattr(resp, "status", 200) and resp.status >= 400:
            raise PreflightError(f"http: {url} -> HTTP {resp.status}")
        return resp.read()


def _log(log_dir: Path | None, name: str, content: str) -> None:
    if log_dir is None:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / name).write_text(content, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Основной прогон.
# ---------------------------------------------------------------------------
def probe(cfg: dict, runner=None, fetcher=None, log_dir=None) -> dict:
    """Выполнить preflight. Возвращает evidence-dict; ok=False => submit запрещён."""
    runner = runner or SubprocessRunner()
    fetcher = fetcher or urllib_fetch
    log_dir = Path(log_dir) if log_dir else None
    reasons: list[str] = []
    steps: list[dict] = []
    evidence: dict = {}
    started = time.time()

    name = cfg["package"]
    base = cfg["repo_url"].rstrip("/")
    alias = cfg.get("alias", "candy-preflight")
    wanted = cfg.get("version")

    def step(sid, ok, reason=None, detail=None, cmd=None, rc=None):
        steps.append({"step": sid, "ok": bool(ok), "reason": reason,
                      "cmd": cmd, "rc": rc, "detail": detail})
        if not ok and reason:
            reasons.append(f"{classify(reason)}: {reason}")

    # 1. HTTP fetch repomd + primary и их разбор.
    repomd_raw = b""
    primary = {}
    primary_href = None
    primary_cksum = None
    try:
        repomd_url = f"{base}/repodata/repomd.xml"
        repomd_raw = fetcher(repomd_url)
        if not repomd_raw:
            raise PreflightError("http: пустой repomd.xml")
        meta = parse_repomd(repomd_raw)
        primary_href = meta["href"]
        primary_cksum = meta["checksum"]
        primary_raw = fetcher(f"{base}/{primary_href}")
        _log(log_dir, "preflight-primary.raw", "bytes=%d href=%s" % (len(primary_raw), primary_href))
        primary_xml = decompress_metadata(primary_raw, primary_href)
        primary = parse_primary(primary_xml, name, wanted)
        evidence["metadata"] = {"primary_href": primary_href,
                                "package_version": primary.get("version"),
                                "package_location": primary.get("location")}
        step("metadata", True, detail=primary_href)
    except PreflightError as exc:
        step("metadata", False, reason=str(exc))
    except Exception as exc:  # noqa: BLE001 — сеть/URL => red
        step("metadata", False, reason=f"transport: не скачалась metadata: {exc}")

    # 2. zypper: добавить repo, refresh, скачать пакет (download-only).
    download_ok = False
    if not reasons:
        cmds = []
        if cfg.get("repo_url"):
            add = ["zypper", "-n", "ar", "-f", base, alias]
            rc, out, err = runner.run(add, timeout=300)
            _log(log_dir, "preflight-zypper-addrepo.log", out + err)
            cmds.append(" ".join(add))
            # уже существует — не ошибка
            if rc != 0 and "already exists" not in (out + err).lower():
                step("zypper-addrepo", False,
                     reason=f"zypper: addrepo rc={rc}: {(err or out).strip()[:200]}",
                     cmd=" ".join(add), rc=rc)
        if not reasons:
            # COPR-репозитории подписаны ключом проекта; без импорта zypper
            # валит refresh на проверке подписи repomd.xml.
            key_url = copr_pubkey_url(base)
            if key_url:
                key_path = str((log_dir or Path(".")) / "copr-pubkey.gpg")
                try:
                    key_raw = fetcher(key_url)
                    if log_dir:
                        log_dir.mkdir(parents=True, exist_ok=True)
                    with open(key_path, "wb") as fh:
                        fh.write(key_raw)
                    rc, out, err = runner.run(["rpm", "--import", key_path], timeout=60)
                    if rc != 0:
                        step("pubkey", False,
                             reason="transport: rpm --import ключа COPR "
                                    f"rc={rc}: {(err or out).strip()[:200]}",
                             cmd="rpm --import copr-pubkey.gpg", rc=rc)
                    else:
                        step("pubkey", True, detail=key_url)
                except PreflightError as exc:
                    step("pubkey", False, reason=str(exc))
                except Exception as exc:  # noqa: BLE001 — ключ недоступен => red
                    step("pubkey", False,
                         reason=f"transport: не скачался ключ COPR: {exc}")
        if not reasons:
            # У COPR openSUSE-репозиториев НЕТ отсоединённой подписи repomd
            # (repomd.xml.asc -> 404), поэтому zypper не может её проверить и
            # валит refresh на «Signature verification failed for repomd.xml».
            # Целостность при этом всё равно проверяем сами: sha256 скачанного
            # пакета обязан совпасть с checksum из primary.xml + rpm -K.
            refresh = ["zypper", "-n", "--no-gpg-checks", "refresh", alias]
            rc, out, err = runner.run(refresh, timeout=900)
            _log(log_dir, "preflight-zypper-refresh.log", out + err)
            if rc != 0:
                step("zypper-refresh", False,
                     reason=f"zypper: refresh rc={rc}: {(err or out).strip()[:300]}",
                     cmd=" ".join(refresh), rc=rc)
            else:
                step("zypper-refresh", True, cmd=" ".join(refresh), rc=rc)
        if not reasons:
            # --from alias обязателен: имя пакета может существовать и в
            # основных репозиториях Tumbleweed, и без этого zypper скачает его
            # оттуда, а мы сравним checksum с COPR-метаданными и получим ложный
            # mismatch. Нам нужен именно артефакт целевого COPR-репозитория.
            dl = ["zypper", "-n", "--no-gpg-checks", "install", "--download-only",
                  "-y", "--from", alias,
                  f"{name}{'=' + wanted if wanted else ''}"]
            rc, out, err = runner.run(dl, timeout=900)
            _log(log_dir, "preflight-zypper-download.log", out + err)
            if rc != 0:
                step("zypper-download", False,
                     reason=f"zypper: download {name} rc={rc}: {(err or out).strip()[:300]}",
                     cmd=" ".join(dl), rc=rc)
            else:
                step("zypper-download", True, cmd=" ".join(dl), rc=rc)
                download_ok = True

    # 3. content: найти скачанный rpm, сверить digest и metadata-checksum.
    if not reasons and download_ok:
        rpm_path = _find_downloaded(runner, alias, name)
        if not rpm_path:
            step("content-find", False, reason="content: скачанный rpm не найден в кэше zypp")
        else:
            try:
                got_name, got_ver = parse_rpm_nv(runner, rpm_path)
                if got_name != name:
                    raise PreflightError(
                        f"content: скачан не тот пакет: {got_name} != {name}")
                if wanted and got_ver != wanted:
                    raise PreflightError(
                        f"content: версия скачанного {got_ver} != ожидаемой {wanted}")
                verify_rpm_digest(runner, rpm_path)
                got_sha = sha256_file(rpm_path)
                if primary.get("checksum"):
                    if (primary.get("checksum_type") or "sha256") != "sha256":
                        raise PreflightError(
                            f"metadata: checksum type {primary.get('checksum_type')!r} "
                            "не sha256 — согласованность не подтверждена")
                    if got_sha != primary["checksum"]:
                        raise PreflightError(
                            "checksum: sha256 скачанного пакета не совпал с metadata "
                            f"({got_sha[:12]}… != {primary['checksum'][:12]}…)")
                evidence["content"] = {"path": rpm_path, "sha256": got_sha,
                                       "version": got_ver}
                step("content", True, detail=got_sha[:12])
            except PreflightError as exc:
                step("content", False, reason=str(exc))

    ok = not reasons
    result = {
        "ok": ok,
        "chroot": cfg.get("chroot"),
        "repo_url": base,
        "package": name,
        "version": wanted,
        "reasons": reasons,
        "steps": steps,
        "evidence": evidence,
        "fingerprint": fingerprint(repomd_raw, primary_cksum),
        "checked_at": int(time.time()),
        "duration_s": round(time.time() - started, 3),
    }
    _log(log_dir, "preflight.json", json.dumps(result, ensure_ascii=False, indent=2))
    return result


def _find_downloaded(runner, alias: str, name: str) -> str | None:
    """Найти скачанный пакет в кэше zypp (путь зависит от версии zypper)."""
    import glob as _glob
    for pat in (f"/var/cache/zypp/packages/{alias}/**/{name}-*.rpm",
                f"/var/cache/zypp/packages/*/**/{name}-*.rpm"):
        hits = sorted(_glob.glob(pat, recursive=True))
        if hits:
            return hits[-1]
    return None


def build_argparser():
    ap = argparse.ArgumentParser(prog="preflight.py",
                                 description="fail-closed openSUSE mirror preflight")
    ap.add_argument("--repo-url", required=True,
                    help="базовый URL repodata (download.copr.../chroot)")
    ap.add_argument("--alias", default="candy-preflight")
    ap.add_argument("--chroot", default="")
    ap.add_argument("--package", required=True)
    ap.add_argument("--version", default=None)
    ap.add_argument("--out", default=None, help="куда записать JSON evidence")
    ap.add_argument("--log-dir", default="logs")
    return ap


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)
    cfg = {"repo_url": args.repo_url, "alias": args.alias, "chroot": args.chroot,
           "package": args.package, "version": args.version}
    result = probe(cfg, log_dir=args.log_dir)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if result["ok"]:
        print(f"PREFLIGHT-OK {args.chroot} {args.package}")
        return 0
    print("PREFLIGHT-FAILED: " + "; ".join(result["reasons"]), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
