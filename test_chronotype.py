# -*- coding: utf-8 -*-
"""TDD tests for chronotype greeting-window helpers."""
from __future__ import annotations

import unittest

from chronotype import (
    DEFAULT_EVENING,
    DEFAULT_MORNING,
    DEFAULT_SLEEP_MINUTE,
    DEFAULT_WAKE_MINUTE,
    SHIFT_MAX,
    SHIFT_MIN,
    apply_explicit_tell,
    clamp_shift,
    empty_chronotype,
    note_hour_activity,
    parse_explicit_tell,
    resolve_wake_sleep,
    shift_greeting_windows,
)


class TestDefaults(unittest.TestCase):
    def test_constant_anchors(self):
        self.assertEqual(DEFAULT_WAKE_MINUTE, 450)
        self.assertEqual(DEFAULT_SLEEP_MINUTE, 1350)
        self.assertEqual(DEFAULT_MORNING, (450, 570))
        self.assertEqual(DEFAULT_EVENING, (1290, 1380))
        self.assertEqual((SHIFT_MIN, SHIFT_MAX), (-180, 360))

    def test_resolve_wake_sleep_none_is_defaults(self):
        self.assertEqual(resolve_wake_sleep(None), (450, 1350))

    def test_shift_greeting_windows_default_anchors(self):
        windows = shift_greeting_windows(450, 1350)
        self.assertEqual(windows["morning"], (450, 570))
        self.assertEqual(windows["evening"], (1290, 1380))


class TestParseExplicitTell(unittest.TestCase):
    def test_habit_sleep_two_oclock(self):
        self.assertEqual(
            parse_explicit_tell("我一般两点睡"),
            {"kind": "sleep", "minute": 120},
        )

    def test_one_off_today_is_not_habit(self):
        self.assertIsNone(parse_explicit_tell("我今天两点才睡"))

    def test_bare_time_without_self_and_habit_is_none(self):
        self.assertIsNone(parse_explicit_tell("两点睡"))

    def test_habit_wake_seven_thirty(self):
        self.assertEqual(
            parse_explicit_tell("我通常七点半起"),
            {"kind": "wake", "minute": 450},
        )

    def test_eleven_sleep_means_twenty_three(self):
        self.assertEqual(
            parse_explicit_tell("我平时11点睡"),
            {"kind": "sleep", "minute": 23 * 60},
        )

    def test_arabic_two_sleep_is_early_morning(self):
        self.assertEqual(
            parse_explicit_tell("俺习惯2点睡"),
            {"kind": "sleep", "minute": 120},
        )


class TestEmptyAndApply(unittest.TestCase):
    def test_empty_chronotype_shape(self):
        chrono = empty_chronotype()
        self.assertIsNone(chrono["explicit_sleep_minute"])
        self.assertIsNone(chrono["explicit_wake_minute"])
        self.assertEqual(chrono["explicit_ts"], 0)
        self.assertEqual(chrono["hour_hist"], {})
        self.assertEqual(chrono["learned_shift_minutes"], 0)
        self.assertEqual(chrono["active_days"], [])

    def test_apply_explicit_tell_sets_sleep_and_ts(self):
        chrono = empty_chronotype()
        out = apply_explicit_tell(chrono, "我一般两点睡", 1700000000.0)
        self.assertIs(out, chrono)
        self.assertEqual(chrono["explicit_sleep_minute"], 120)
        self.assertEqual(chrono["explicit_ts"], 1700000000.0)

    def test_apply_non_habit_does_not_mutate_explicit(self):
        chrono = empty_chronotype()
        apply_explicit_tell(chrono, "我今天两点才睡", 99.0)
        self.assertIsNone(chrono["explicit_sleep_minute"])
        self.assertEqual(chrono["explicit_ts"], 0)


class TestClampShift(unittest.TestCase):
    def test_clamp_below_min(self):
        self.assertEqual(clamp_shift(-181), -180)

    def test_clamp_above_max(self):
        self.assertEqual(clamp_shift(361), 360)

    def test_clamp_in_range_passthrough(self):
        self.assertEqual(clamp_shift(0), 0)
        self.assertEqual(clamp_shift(-180), -180)
        self.assertEqual(clamp_shift(360), 360)


class TestHistogramLearner(unittest.TestCase):
    def test_four_distinct_dates_do_not_learn(self):
        chrono = empty_chronotype()
        for day in ("2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27"):
            note_hour_activity(chrono, 22, day)
        self.assertEqual(chrono["learned_shift_minutes"], 0)
        self.assertEqual(len(chrono["active_days"]), 4)

    def test_fifth_distinct_date_may_learn_and_stays_clamped(self):
        chrono = empty_chronotype()
        days = [
            "2026-08-24",
            "2026-08-25",
            "2026-08-26",
            "2026-08-27",
            "2026-08-28",
        ]
        for day in days:
            note_hour_activity(chrono, 22, day)
        self.assertEqual(len(chrono["active_days"]), 5)
        shift = chrono["learned_shift_minutes"]
        self.assertGreaterEqual(shift, -180)
        self.assertLessEqual(shift, 360)
        # night-owl prime hour 22 => (22*60+30) - 1350 = 0
        self.assertEqual(shift, 0)

    def test_same_hour_same_day_does_not_increment_n(self):
        chrono = empty_chronotype()
        note_hour_activity(chrono, 8, "2026-08-28")
        note_hour_activity(chrono, 8, "2026-08-28")
        self.assertEqual(chrono["hour_hist"]["8"]["n"], 1)
        self.assertEqual(chrono["hour_hist"]["8"]["last_day"], "2026-08-28")
        self.assertEqual(chrono["active_days"], ["2026-08-28"])

    def test_active_days_keep_last_14(self):
        chrono = empty_chronotype()
        for i in range(1, 16):
            note_hour_activity(chrono, 9, f"2026-08-{i:02d}")
        self.assertEqual(len(chrono["active_days"]), 14)
        self.assertEqual(chrono["active_days"][0], "2026-08-02")
        self.assertEqual(chrono["active_days"][-1], "2026-08-15")

    def test_learned_shift_always_clamped_even_if_extreme_hour(self):
        chrono = empty_chronotype()
        for i in range(5):
            note_hour_activity(chrono, 23, f"2026-08-{20 + i:02d}")
        self.assertGreaterEqual(chrono["learned_shift_minutes"], -180)
        self.assertLessEqual(chrono["learned_shift_minutes"], 360)


class TestResolveAndWindows(unittest.TestCase):
    def test_explicit_overrides_defaults(self):
        chrono = empty_chronotype()
        chrono["explicit_wake_minute"] = 480
        chrono["explicit_sleep_minute"] = 1410
        self.assertEqual(resolve_wake_sleep(chrono), (480, 1410))

    def test_learned_shift_applied_when_no_explicit(self):
        chrono = empty_chronotype()
        chrono["learned_shift_minutes"] = 60
        self.assertEqual(resolve_wake_sleep(chrono), (510, 1410))

    def test_shift_greeting_windows_plus_sixty(self):
        default = shift_greeting_windows(450, 1350)
        shifted = shift_greeting_windows(450 + 60, 1350 + 60)
        self.assertEqual(shifted["morning"][0], default["morning"][0] + 60)
        self.assertEqual(shifted["morning"], (510, 630))
        self.assertEqual(shifted["evening"], (1350, 1440))


if __name__ == "__main__":
    unittest.main()
