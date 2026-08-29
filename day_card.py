# -*- coding: utf-8 -*-
"""Daily life-card drawer: vary LLM daily plans with a scene + two events."""
from __future__ import annotations

import random
from typing import Any, Optional

COOLDOWN_DAYS = 7

SCENES: list[dict[str, str]] = [
    {
        "id": "rainy_home",
        "title": "下雨困在家",
        "setting": "一整天都出不去，窗外一直下雨，屋里潮潮的只能窝着。",
    },
    {
        "id": "friend_city",
        "title": "去同学城市玩一天",
        "setting": "坐车去同学所在的城市晃一天，路线陌生，时间被交通和见面占满。",
    },
    {
        "id": "all_nighter",
        "title": "通宵后的废日",
        "setting": "昨晚几乎没睡，白天脑子发木，日程只能是低强度的残局。",
    },
    {
        "id": "club_rehearsal",
        "title": "社团彩排",
        "setting": "大半天耗在排练室，灯光、队形和反复走位把时间切碎。",
    },
    {
        "id": "exam_week",
        "title": "考试周",
        "setting": "教室、图书馆、复习资料轮转，心情紧，空档也围着考试转。",
    },
    {
        "id": "weekend_market",
        "title": "周末赶集",
        "setting": "早起去市集或夜市逛摊，人多、东西杂，一天被逛和吃填满。",
    },
    {
        "id": "sick_day",
        "title": "生病躺床",
        "setting": "发烧或感冒躺着，几乎不出门，行动半径只剩床和热水。",
    },
    {
        "id": "overnight_train",
        "title": "夜车赶路",
        "setting": "坐夜车或过夜大巴赶路，白天是车厢、站台和迟到的困倦。",
    },
]

EVENTS: list[dict[str, str]] = [
    {"id": "lost_parcel", "blurb": "快递丢了"},
    {"id": "power_cut", "blurb": "突然停电"},
    {"id": "friend_cancel", "blurb": "朋友放鸽子"},
    {"id": "old_photo", "blurb": "捡到旧照片"},
    {"id": "neighbor_noise", "blurb": "邻居装修"},
    {"id": "extra_shift", "blurb": "被拉去加班"},
    {"id": "stray_cat", "blurb": "楼下出现一只猫"},
    {"id": "group_project", "blurb": "小组作业爆了"},
]

_SCENE_BY_ID = {s["id"]: s for s in SCENES}
_EVENT_BY_ID = {e["id"]: e for e in EVENTS}


def empty_history() -> dict[str, list[str]]:
    return {"recent_scene_ids": [], "recent_event_ids": []}


def _rng_for(date_key: str, rng: Optional[random.Random]) -> random.Random:
    if rng is None:
        return random.Random(date_key)
    return rng


def _pick_scene(history: dict, rng: random.Random) -> dict[str, str]:
    recent = list(history.get("recent_scene_ids") or [])
    cooled = set(recent[-COOLDOWN_DAYS:])
    available = [s for s in SCENES if s["id"] not in cooled]
    if available:
        return rng.choice(available)
    # All on cooldown: pick the oldest-cooldown (first in recent window), never fail.
    if recent:
        oldest = recent[-COOLDOWN_DAYS:][0] if len(recent) >= COOLDOWN_DAYS else recent[0]
        scene = _SCENE_BY_ID.get(oldest)
        if scene is not None:
            return scene
    return SCENES[0]


def _pick_events(history: dict, rng: random.Random) -> list[dict[str, str]]:
    recent = list(history.get("recent_event_ids") or [])
    cooled = set(recent[-14:])
    available = [e for e in EVENTS if e["id"] not in cooled]
    picked: list[dict[str, str]] = []
    pool = list(available)
    rng.shuffle(pool)
    for ev in pool:
        if ev["id"] not in {p["id"] for p in picked}:
            picked.append(ev)
        if len(picked) == 2:
            return picked
    # Pool exhausted: allow reuse, still return 2 distinct ids if possible.
    leftovers = [e for e in EVENTS if e["id"] not in {p["id"] for p in picked}]
    rng.shuffle(leftovers)
    for ev in leftovers:
        picked.append(ev)
        if len(picked) == 2:
            break
    while len(picked) < 2:
        picked.append(EVENTS[len(picked) % len(EVENTS)])
    return picked[:2]


def draw_day_card(
    history: dict,
    *,
    date_key: str,
    rng: Optional[random.Random] = None,
) -> dict[str, Any]:
    r = _rng_for(date_key, rng)
    scene = _pick_scene(history, r)
    events = _pick_events(history, r)
    return {
        "date": date_key,
        "scene": {"id": scene["id"], "title": scene["title"], "setting": scene["setting"]},
        "events": [{"id": e["id"], "blurb": e["blurb"]} for e in events],
    }


def remember_card(history: dict, card: dict) -> dict:
    scenes = list(history.get("recent_scene_ids") or [])
    events = list(history.get("recent_event_ids") or [])
    scenes.append(card["scene"]["id"])
    events.append(card["events"][0]["id"])
    events.append(card["events"][1]["id"])
    history["recent_scene_ids"] = scenes[-14:]
    history["recent_event_ids"] = events[-28:]
    return history


def format_card_for_prompt(card: dict) -> str:
    title = card["scene"]["title"]
    setting = card["scene"]["setting"]
    b1 = card["events"][0]["blurb"]
    b2 = card["events"][1]["blurb"]
    return (
        f"今日场景：{title}。场景约束：{setting}。\n"
        f"今日事件：1) {b1} 2) {b2}\n"
        "要求：日程必须围绕这个场景来写，并把两个事件嵌进不同时段；"
        "不要写成与近几天相同的上课/写代码模板日。"
    )


def format_card_for_user(card: dict) -> str:
    title = card["scene"]["title"]
    b1 = card["events"][0]["blurb"]
    b2 = card["events"][1]["blurb"]
    return f"今日人生卡：{title}｜{b1}；{b2}"
