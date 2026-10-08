#!/usr/bin/env python3
"""retry_brake.py — защита от blind-retry и build storm в openSUSE-волне.

Две независимые тормозные системы:

1. cooldown на уровне (name, target): терминально упавший target НЕ
   переотправляется на следующий день автоматически. Повтор разрешён только
   если (а) preflight зелёный и (б) прошёл cooldown с прошлого падения.

2. mirror-brake: если preflight падает с ОДИНАКОВОЙ подписью зеркала
   (см. ``preflight.fingerprint``) ``threshold`` раз подряд, поднимается
   общий тормоз ``armed`` — submit'ы запрещены до ручного сброса. Это не даёт
   внешнему сбою зеркала породить лавину билдов.

Файл состояния: ``state/retry-brake.json`` (кладётся в артефакт/ветку).
Чистые функции не зависят от сети/времени — время приходит аргументом.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

DEFAULT_PATH = "state/retry-brake.json"
DEFAULT_COOLDOWN_MIN = 1440
DEFAULT_MIRROR_THRESHOLD = 2


def load(path=DEFAULT_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        return {"version": 1, "armed": False, "mirror": {}, "packages": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "armed": False, "mirror": {}, "packages": {}}
    data.setdefault("version", 1)
    data.setdefault("armed", False)
    data.setdefault("mirror", {})
    data.setdefault("packages", {})
    return data


def save(path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")


def _key(name: str, target: str) -> str:
    return f"{name}@{target}"


def cooldown_ok(data: dict, name: str, target: str, now: int | None = None,
                cooldown_min: int = DEFAULT_COOLDOWN_MIN) -> tuple[bool, str]:
    """Разрешён ли повтор для (name, target). Возвращает (ok, reason)."""
    now = int(time.time()) if now is None else int(now)
    entry = data.get("packages", {}).get(_key(name, target))
    if not entry or entry.get("last_state") != "failed":
        return True, "нет терминального падения"
    last = int(entry.get("last_fail_ts", 0))
    age_min = (now - last) / 60.0
    if cooldown_min <= 0:
        return True, "cooldown отключён"
    if age_min >= cooldown_min:
        return True, f"cooldown истёк ({age_min:.0f}м >= {cooldown_min}м)"
    return False, (f"cooldown активен: {age_min:.0f}м < {cooldown_min}м "
                   f"после падения (нужен ручной запуск)")


def record_failure(data: dict, name: str, target: str,
                   now: int | None = None) -> dict:
    now = int(time.time()) if now is None else int(now)
    entry = data.setdefault("packages", {}).setdefault(_key(name, target), {})
    entry["last_state"] = "failed"
    entry["last_fail_ts"] = now
    entry["fail_count"] = int(entry.get("fail_count", 0)) + 1
    return data


def record_success(data: dict, name: str, target: str,
                   now: int | None = None) -> dict:
    entry = data.setdefault("packages", {}).setdefault(_key(name, target), {})
    entry["last_state"] = "ok"
    entry["last_ok_ts"] = int(time.time()) if now is None else int(now)
    entry["fail_count"] = 0
    return data


def observe_mirror(data: dict, fingerprint: str, ok: bool,
                   threshold: int = DEFAULT_MIRROR_THRESHOLD) -> dict:
    """Отметить результат preflight. Одинаковый сбой копится -> armed.

    Успешный preflight сбрасывает счётчик ДАННОЙ подписи; другие подписи
    сохраняются, чтобы смена характера сбоя не обнуляла тормоз.
    """
    if ok:
        data.get("mirror", {}).pop(fingerprint, None)
        return data
    m = data.setdefault("mirror", {}).setdefault(
        fingerprint, {"count": 0, "first_ts": int(time.time())})
    m["count"] = int(m.get("count", 0)) + 1
    m["last_ts"] = int(time.time())
    if m["count"] >= threshold:
        data["armed"] = True
    return data


def submit_allowed(data: dict, name: str, target: str,
                   now: int | None = None,
                   cooldown_min: int = DEFAULT_COOLDOWN_MIN) -> tuple[bool, str]:
    """Итоговое решение: armed? cooldown? Можно ли submit'ить этот target."""
    if data.get("armed"):
        return False, "mirror-brake взведён: повторный внешний сбой зеркала"
    return cooldown_ok(data, name, target, now=now, cooldown_min=cooldown_min)


def reset(data: dict) -> dict:
    data["armed"] = False
    data["mirror"] = {}
    return data


def build_argparser():
    ap = argparse.ArgumentParser(prog="retry-brake")
    ap.add_argument("--file", default=DEFAULT_PATH)
    ap.add_argument("--cooldown-min", type=int, default=DEFAULT_COOLDOWN_MIN)
    ap.add_argument("--threshold", type=int, default=DEFAULT_MIRROR_THRESHOLD)
    sub = ap.add_subparsers(dest="cmd", required=True)

    chk = sub.add_parser("check")
    chk.add_argument("--name", required=True)
    chk.add_argument("--target", required=True)

    rf = sub.add_parser("record-failure")
    rf.add_argument("--name", required=True)
    rf.add_argument("--target", required=True)

    rs = sub.add_parser("record-success")
    rs.add_argument("--name", required=True)
    rs.add_argument("--target", required=True)

    ob = sub.add_parser("observe-mirror")
    ob.add_argument("--fingerprint", required=True)
    ob.add_argument("--ok", choices=["true", "false"], required=True)

    sub.add_parser("reset")
    return ap


def main(argv=None) -> int:
    args = build_argparser().parse_args(argv)
    data = load(args.file)
    if args.cmd == "check":
        ok, reason = submit_allowed(data, args.name, args.target,
                                    cooldown_min=args.cooldown_min)
        print(("ALLOW " if ok else "BLOCK ") + reason)
        return 0 if ok else 1
    if args.cmd == "record-failure":
        save(args.file, record_failure(data, args.name, args.target))
    elif args.cmd == "record-success":
        save(args.file, record_success(data, args.name, args.target))
    elif args.cmd == "observe-mirror":
        save(args.file, observe_mirror(data, args.fingerprint,
                                       args.ok == "true", threshold=args.threshold))
    elif args.cmd == "reset":
        save(args.file, reset(data))
    print("OK", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
