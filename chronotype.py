# -*- coding: utf-8 -*-
"""Pure-function chronotype helpers for greeting windows."""
from __future__ import annotations

import re

DEFAULT_WAKE_MINUTE = 7 * 60 + 30  # 450
DEFAULT_SLEEP_MINUTE = 22 * 60 + 30  # 1350
DEFAULT_MORNING = (7 * 60 + 30, 9 * 60 + 30)  # 450, 570
DEFAULT_EVENING = (21 * 60 + 30, 23 * 60)  # 1290, 1380
SHIFT_MIN, SHIFT_MAX = -180, 360

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_SELF_HABIT = re.compile(
    r"(我|俺|人家|本人)(一般|通常|平时|习惯|总是|基本上?|大多|经常)"
)
_TIME_VERB = re.compile(
    r"(凌晨|早上|早晨|清晨|上午|中午|午后|下午|傍晚|晚上|夜里|半夜|深夜)?"
    r"([0-9]{1,2}|[零一二两三四五六七八九十]+)"
    r"[点时:：]"
    r"(半)?"
    r"(左右|多|才|再|过后|之后)?"
    r"(睡|睡觉|入睡|就寝|歇|起床|起来|起|睡醒|醒来|醒)"
)
_WAKE_VERBS = {"起床", "起来", "起", "睡醒", "醒来", "醒"}
_LEARN_MIN_DAYS = 5
_MAX_ACTIVE_DAYS = 14


def _cn_number_to_int(text: str) -> int | None:
    text = str(text or "").strip()
    if not text:
        return None
    if text.isdigit():
        value = int(text)
        return value if 0 <= value <= 24 else None
    if text in _CN_DIGITS:
        return _CN_DIGITS[text]
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        value = tens * 10 + ones
        return value if 0 <= value <= 24 else None
    return None


def parse_explicit_tell(text: str) -> dict | None:
    raw = str(text or "")
    if not raw:
        return None
    for match in _TIME_VERB.finditer(raw):
        window = raw[max(0, match.start() - 12) : match.start()]
        if not _SELF_HABIT.search(window):
            continue
        daypart = match.group(1) or ""
        hour = _cn_number_to_int(match.group(2) or "")
        if hour is None:
            continue
        minute = 30 if match.group(3) == "半" else 0
        verb = match.group(5) or ""
        is_wake = verb in _WAKE_VERBS
        if daypart in {"中午", "午后"}:
            if hour == 12:
                hour = 12
            elif 0 < hour < 12:
                hour += 12
        elif daypart in {"下午", "傍晚"} and 0 < hour < 12:
            hour += 12
        elif daypart in {"晚上", "夜里", "半夜", "深夜"} and 0 < hour < 12:
            hour += 12
        elif not daypart:
            if is_wake and hour >= 20:
                hour -= 12
            if not is_wake and 7 <= hour <= 12:
                hour += 12
        hour = hour % 24
        kind = "wake" if is_wake else "sleep"
        return {"kind": kind, "minute": hour * 60 + minute}
    return None


def empty_chronotype() -> dict:
    return {
        "explicit_sleep_minute": None,
        "explicit_wake_minute": None,
        "explicit_ts": 0,
        "hour_hist": {},
        "learned_shift_minutes": 0,
        "active_days": [],
    }


def apply_explicit_tell(chrono: dict, text: str, now_ts: float) -> dict:
    tell = parse_explicit_tell(text)
    if tell:
        if tell["kind"] == "wake":
            chrono["explicit_wake_minute"] = int(tell["minute"])
        else:
            chrono["explicit_sleep_minute"] = int(tell["minute"])
        chrono["explicit_ts"] = now_ts
    return chrono


def _refresh_learned_shift(chrono: dict) -> None:
    if len(chrono.get("active_days") or []) < _LEARN_MIN_DAYS:
        chrono["learned_shift_minutes"] = 0
        return
    hist = chrono.get("hour_hist") or {}
    best_hour = None
    best_n = -1
    for key, bucket in hist.items():
        n = int((bucket or {}).get("n") or 0)
        if n > best_n:
            best_n = n
            best_hour = int(key)
    if best_hour is None or best_n <= 0:
        chrono["learned_shift_minutes"] = 0
        return
    if best_hour >= 18:
        shift = (best_hour * 60 + 30) - DEFAULT_SLEEP_MINUTE
    else:
        shift = best_hour * 60 - DEFAULT_WAKE_MINUTE
    chrono["learned_shift_minutes"] = clamp_shift(shift)


def note_hour_activity(chrono: dict, local_hour: int, day: str) -> dict:
    hour = int(local_hour)
    key = str(hour)
    hist = chrono.setdefault("hour_hist", {})
    bucket = hist.get(key)
    if not isinstance(bucket, dict):
        bucket = {"n": 0, "last_day": ""}
        hist[key] = bucket
    if bucket.get("last_day") != day:
        bucket["n"] = int(bucket.get("n") or 0) + 1
        bucket["last_day"] = day
    days = chrono.setdefault("active_days", [])
    if day not in days:
        days.append(day)
        chrono["active_days"] = days[-_MAX_ACTIVE_DAYS:]
    _refresh_learned_shift(chrono)
    return chrono


def clamp_shift(minutes: int) -> int:
    return max(SHIFT_MIN, min(SHIFT_MAX, int(minutes)))


def resolve_wake_sleep(chrono: dict | None) -> tuple[int, int]:
    if not chrono:
        return DEFAULT_WAKE_MINUTE, DEFAULT_SLEEP_MINUTE
    shift = clamp_shift(int(chrono.get("learned_shift_minutes") or 0))
    wake = chrono.get("explicit_wake_minute")
    sleep = chrono.get("explicit_sleep_minute")
    if wake is None:
        wake = DEFAULT_WAKE_MINUTE + shift
    if sleep is None:
        sleep = DEFAULT_SLEEP_MINUTE + shift
    return int(wake), int(sleep)


def shift_greeting_windows(wake_minute: int, sleep_minute: int) -> dict:
    shift = clamp_shift(int(wake_minute) - DEFAULT_WAKE_MINUTE)
    morning = (DEFAULT_MORNING[0] + shift, DEFAULT_MORNING[1] + shift)
    evening = (DEFAULT_EVENING[0] + shift, DEFAULT_EVENING[1] + shift)
    return {"morning": morning, "evening": evening}
