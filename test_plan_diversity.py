# -*- coding: utf-8 -*-
import unittest

from plan_diversity import (
    TEMPLATE_MARKERS,
    format_avoid_block,
    looks_like_template_day,
    recent_activity_lines,
    sleep_event_count,
)


def _plan(*activities, date=None):
    payload = {"events": [{"activity": a} for a in activities]}
    if date is not None:
        payload["date"] = date
    return payload


class TestRecentActivityLines(unittest.TestCase):
    def test_takes_last_three_plans_activities_without_dates(self):
        plans = [
            _plan("很久以前的课"),
            _plan("前天社团彩排"),
            _plan("昨天楼下出现一只猫", "昨天写作业"),
            _plan("今天午睡"),
        ]
        lines = recent_activity_lines(plans, limit_days=3)
        self.assertEqual(
            lines,
            ["前天社团彩排", "昨天楼下出现一只猫", "昨天写作业", "今天午睡"],
        )
        self.assertNotIn("很久以前的课", lines)

    def test_skips_empty_and_collapses_whitespace(self):
        plans = [
            _plan("  上午上课  ", "", "写  代码", "   "),
        ]
        lines = recent_activity_lines(plans, limit_days=3)
        self.assertEqual(lines, ["上午上课", "写 代码"])

    def test_uses_most_recent_dates_regardless_of_list_order(self):
        plans = [
            _plan("今天喂猫", date="2026-08-29"),
            _plan("前天上课", date="2026-08-27"),
            _plan("大前天写代码", date="2026-08-26"),
            _plan("昨天买菜", date="2026-08-28"),
        ]
        lines = recent_activity_lines(plans, limit_days=3)
        self.assertEqual(lines, ["前天上课", "昨天买菜", "今天喂猫"])
        self.assertNotIn("大前天写代码", lines)


class TestFormatAvoidBlock(unittest.TestCase):
    def test_empty_returns_empty_string(self):
        self.assertEqual(format_avoid_block([]), "")

    def test_lists_activity_and_asks_not_to_repeat(self):
        block = format_avoid_block(["上午上课"])
        self.assertIn("上午上课", block)
        self.assertTrue("不要重复" in block or "避免重复" in block)
        self.assertNotIn("http", block.lower())
        self.assertNotRegex(block, r"\d+\.\d+\.\d+\.\d+")

    def test_caps_at_max_items(self):
        activities = [f"活动{i}" for i in range(12)]
        block = format_avoid_block(activities, max_items=8)
        self.assertIn("活动0", block)
        self.assertIn("活动7", block)
        self.assertNotIn("活动8", block)
        self.assertTrue("不要重复" in block or "避免重复" in block)


class TestLooksLikeTemplateDay(unittest.TestCase):
    def test_true_when_two_template_marker_events(self):
        events = [
            {"activity": "上课听讲"},
            {"activity": "写代码赶需求"},
            {"activity": "睡觉"},
        ]
        self.assertTrue(looks_like_template_day(events))

    def test_false_for_specific_non_template_day(self):
        events = [
            {"activity": "社团彩排卡住了"},
            {"activity": "楼下出现一只猫"},
            {"activity": "睡觉"},
        ]
        self.assertFalse(looks_like_template_day(events))

    def test_false_for_all_sleep(self):
        events = [
            {"activity": "睡觉"},
            {"activity": "午睡"},
            {"activity": "休息"},
        ]
        self.assertFalse(looks_like_template_day(events))

    def test_true_when_all_non_sleep_are_template_markers(self):
        events = [
            {"activity": "在家处理自己手头的事"},
            {"activity": "午睡"},
            {"activity": "睡觉"},
        ]
        self.assertTrue(looks_like_template_day(events))

    def test_markers_include_required_strings(self):
        for marker in ("上课", "写代码", "在家处理自己手头的事", "窝着刷手机"):
            self.assertIn(marker, TEMPLATE_MARKERS)


class TestSleepEventCount(unittest.TestCase):
    def test_counts_activities_containing_sleep_char(self):
        events = [
            {"activity": "上课听讲"},
            {"activity": "午睡补觉"},
            {"activity": "睡觉"},
            {"activity": "楼下出现一只猫"},
        ]
        self.assertEqual(sleep_event_count(events), 2)


if __name__ == "__main__":
    unittest.main()
