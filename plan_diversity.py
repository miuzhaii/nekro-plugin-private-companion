# -*- coding: utf-8 -*-
"""Daily-plan diversity helpers: recent-activity blacklist and template-day checks."""

from __future__ import annotations

TEMPLATE_MARKERS = (
    "上课",
    "写代码",
    "在家处理自己手头的事",
    "窝着刷手机",
)

_SLEEP_LABELS = ("睡觉", "午睡", "休息")


def _normalize_activity(text: object) -> str:
    return " ".join(str(text or "").split())


def _event_activity(event: object) -> str:
    if isinstance(event, dict):
        return _normalize_activity(event.get("activity"))
    return _normalize_activity(event)


def _has_template_marker(activity: str) -> bool:
    return any(marker in activity for marker in TEMPLATE_MARKERS)


def _is_sleep_or_rest(activity: str) -> bool:
    return any(label in activity for label in _SLEEP_LABELS)


def recent_activity_lines(plans: list, *, limit_days: int = 3) -> list[str]:
    """Flatten activities from the last ``limit_days`` plans."""
    rows = [p for p in (plans or []) if isinstance(p, dict)]
    if not rows or limit_days <= 0:
        return []
    if all(p.get("date") for p in rows):
        selected = sorted(rows, key=lambda p: str(p.get("date")))[-limit_days:]
    else:
        selected = rows[-limit_days:]
    lines: list[str] = []
    for plan in selected:
        for event in plan.get("events") or []:
            activity = _event_activity(event)
            if activity:
                lines.append(activity)
    return lines


def format_avoid_block(activities: list[str], *, max_items: int = 8) -> str:
    """Chinese prompt snippet listing recent activities to avoid repeating."""
    items: list[str] = []
    for raw in activities or []:
        text = _normalize_activity(raw)
        if not text:
            continue
        items.append(text)
        if len(items) >= max_items:
            break
    if not items:
        return ""
    listed = "、".join(items)
    return f"不要重复最近已经做过的安排：{listed}。"


def looks_like_template_day(events: list) -> bool:
    """True when a day collapsed back to class/coding/idle templates."""
    activities = [_event_activity(e) for e in (events or [])]
    activities = [a for a in activities if a]
    if sum(1 for a in activities if _has_template_marker(a)) >= 2:
        return True
    non_sleep = [a for a in activities if not _is_sleep_or_rest(a)]
    if not non_sleep:
        return False
    return all(_has_template_marker(a) for a in non_sleep)


def sleep_event_count(events: list) -> int:
    """Count events whose activity contains 睡 (睡觉 / 午睡)."""
    return sum(1 for e in (events or []) if "睡" in _event_activity(e))
