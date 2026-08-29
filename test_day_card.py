# -*- coding: utf-8 -*-
"""TDD tests for daily life-card drawer (pure functions)."""
from __future__ import annotations

import re
import unittest
from datetime import date, timedelta

from day_card import (
    COOLDOWN_DAYS,
    EVENTS,
    SCENES,
    draw_day_card,
    empty_history,
    format_card_for_prompt,
    format_card_for_user,
    remember_card,
)

IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
HTTP_RE = re.compile(r"https?://", re.IGNORECASE)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


class TestCatalog(unittest.TestCase):
    def test_scenes_have_at_least_eight_dicts_with_required_keys(self):
        self.assertGreaterEqual(len(SCENES), 8)
        ids = []
        for scene in SCENES:
            self.assertIsInstance(scene, dict)
            self.assertIn("id", scene)
            self.assertIn("title", scene)
            self.assertIn("setting", scene)
            self.assertTrue(_has_cjk(scene["title"]))
            self.assertTrue(_has_cjk(scene["setting"]))
            ids.append(scene["id"])
        self.assertEqual(len(ids), len(set(ids)))

    def test_events_have_at_least_eight_dicts_with_required_keys(self):
        self.assertGreaterEqual(len(EVENTS), 8)
        ids = []
        for event in EVENTS:
            self.assertIsInstance(event, dict)
            self.assertIn("id", event)
            self.assertIn("blurb", event)
            self.assertTrue(_has_cjk(event["blurb"]))
            ids.append(event["id"])
        self.assertEqual(len(ids), len(set(ids)))

    def test_cooldown_days_is_seven(self):
        self.assertEqual(COOLDOWN_DAYS, 7)


class TestEmptyHistory(unittest.TestCase):
    def test_empty_history_shape(self):
        hist = empty_history()
        self.assertEqual(hist, {"recent_scene_ids": [], "recent_event_ids": []})
        self.assertIsInstance(hist["recent_scene_ids"], list)
        self.assertIsInstance(hist["recent_event_ids"], list)


class TestDrawDayCard(unittest.TestCase):
    def test_same_date_key_empty_history_is_deterministic(self):
        hist = empty_history()
        a = draw_day_card(hist, date_key="2026-08-30")
        b = draw_day_card(hist, date_key="2026-08-30")
        self.assertEqual(a, b)
        self.assertEqual(a["date"], "2026-08-30")
        self.assertIn("id", a["scene"])
        self.assertIn("title", a["scene"])
        self.assertIn("setting", a["scene"])
        self.assertEqual(len(a["events"]), 2)
        for ev in a["events"]:
            self.assertIn("id", ev)
            self.assertIn("blurb", ev)

    def test_two_events_in_a_card_have_different_ids(self):
        card = draw_day_card(empty_history(), date_key="2026-08-30")
        ids = [ev["id"] for ev in card["events"]]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(ids[0], ids[1])

    def test_remember_then_draw_seven_distinct_dates_does_not_reuse_first_scene(self):
        hist = empty_history()
        start = date(2026, 8, 30)
        first = draw_day_card(hist, date_key=start.isoformat())
        remember_card(hist, first)
        first_scene = first["scene"]["id"]
        later_scenes = []
        for i in range(1, 8):
            day = start + timedelta(days=i)
            card = draw_day_card(hist, date_key=day.isoformat())
            later_scenes.append(card["scene"]["id"])
            remember_card(hist, card)
        self.assertNotIn(first_scene, later_scenes)

    def test_draw_never_fails_when_all_scenes_on_cooldown(self):
        hist = empty_history()
        start = date(2026, 9, 1)
        for i in range(20):
            day = start + timedelta(days=i)
            card = draw_day_card(hist, date_key=day.isoformat())
            self.assertIn("scene", card)
            self.assertTrue(card["scene"]["id"])
            remember_card(hist, card)


class TestRememberCard(unittest.TestCase):
    def test_remember_appends_and_trims(self):
        hist = empty_history()
        card = draw_day_card(hist, date_key="2026-08-30")
        out = remember_card(hist, card)
        self.assertIs(out, hist)
        self.assertEqual(hist["recent_scene_ids"][-1], card["scene"]["id"])
        self.assertEqual(
            hist["recent_event_ids"][-2:],
            [card["events"][0]["id"], card["events"][1]["id"]],
        )

        for i in range(20):
            extra = {
                "date": f"2026-09-{i+1:02d}",
                "scene": {"id": f"s{i}", "title": "t", "setting": "s"},
                "events": [
                    {"id": f"e{i}a", "blurb": "a"},
                    {"id": f"e{i}b", "blurb": "b"},
                ],
            }
            remember_card(hist, extra)
        self.assertLessEqual(len(hist["recent_scene_ids"]), 14)
        self.assertLessEqual(len(hist["recent_event_ids"]), 28)


class TestFormatters(unittest.TestCase):
    def setUp(self):
        self.card = {
            "date": "2026-08-30",
            "scene": {
                "id": "rainy_home",
                "title": "下雨困在家",
                "setting": "一整天都出不去，窗外一直下雨",
            },
            "events": [
                {"id": "lost_parcel", "blurb": "快递丢了"},
                {"id": "stray_cat", "blurb": "楼下出现一只猫"},
            ],
        }

    def test_format_card_for_prompt_contains_title_and_blurbs_no_http_ipv4(self):
        text = format_card_for_prompt(self.card)
        self.assertIn(self.card["scene"]["title"], text)
        self.assertIn(self.card["events"][0]["blurb"], text)
        self.assertIn(self.card["events"][1]["blurb"], text)
        self.assertTrue(_has_cjk(text))
        self.assertIsNone(HTTP_RE.search(text))
        self.assertIsNone(IPV4_RE.search(text))
        self.assertIn("今日场景", text)
        self.assertIn("今日事件", text)

    def test_format_card_for_user_chinese_no_http(self):
        text = format_card_for_user(self.card)
        self.assertIn(self.card["scene"]["title"], text)
        self.assertIn(self.card["events"][0]["blurb"], text)
        self.assertIn(self.card["events"][1]["blurb"], text)
        self.assertTrue(_has_cjk(text))
        self.assertIsNone(HTTP_RE.search(text))
        self.assertIsNone(IPV4_RE.search(text))
        self.assertLess(len(text), len(format_card_for_prompt(self.card)))


if __name__ == "__main__":
    unittest.main()
