# -*- coding: utf-8 -*-
"""Busy/idle gate: delay passive replies and block proactive messages while busy."""

from __future__ import annotations

import re

# 开周会 / 开早会 / 开例会 — 开会 without the two chars being adjacent
_MEETING_PATTERN = re.compile(r"开.{0,3}会")

BUSY_KEYWORDS = (
    "上课",
    "听课",
    "自习",
    "开会",
    "周会",
    "会议",
    "值班",
    "写代码",
    "编程",
    "赶稿",
    "考试",
    "通勤",
)

EXCLUDE_KEYWORDS = (
    "睡觉",
    "午睡",
    "休息",
    "摸鱼",
    "放松",
    "吃饭",
    "刷视频",
    "打游戏",
    "聊天",
)

URGENT_KEYWORDS = (
    "救命",
    "出事",
    "紧急",
    "医院",
    "受伤",
    "害怕",
    "崩溃",
)


def is_busy_activity(activity: str) -> bool:
    text = str(activity or "")
    if not text.strip():
        return False
    if any(word in text for word in EXCLUDE_KEYWORDS):
        return False
    if any(word in text for word in BUSY_KEYWORDS):
        return True
    return bool(_MEETING_PATTERN.search(text))


def should_block_proactive(schedule: dict, kind: str = "") -> tuple[bool, str]:
    activity = str((schedule or {}).get("activity") or "")
    if not is_busy_activity(activity):
        return False, ""
    # kind is unused: greetings stay blocked while the activity is busy
    _ = kind
    reason = f"正忙着{activity}，先不主动打扰"
    return True, reason


def should_delay_passive_reply(activity: str, user_text: str) -> bool:
    if not is_busy_activity(activity):
        return False
    text = str(user_text or "")
    if any(word in text for word in URGENT_KEYWORDS):
        return False
    return True
