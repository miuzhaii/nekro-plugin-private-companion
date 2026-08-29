# -*- coding: utf-8 -*-
import unittest

from busy_gate import is_busy_activity, should_block_proactive, should_delay_passive_reply


class TestBusyGate(unittest.TestCase):
    def test_class_and_meeting_are_busy(self):
        self.assertTrue(is_busy_activity("上午上课"))
        self.assertTrue(is_busy_activity("开周会"))
        self.assertTrue(is_busy_activity("写代码赶需求"))

    def test_sleep_and_slack_are_not_busy(self):
        self.assertFalse(is_busy_activity("午睡补觉"))
        self.assertFalse(is_busy_activity("躺着刷视频"))
        self.assertFalse(is_busy_activity(""))

    def test_proactive_blocked_when_busy_and_energy_ok(self):
        ok, reason = should_block_proactive({"activity": "上课听讲", "energy": 70}, kind="share_activity")
        self.assertTrue(ok)
        self.assertIn("忙", reason)
        self.assertIn("上课听讲", reason)

    def test_sleep_low_energy_still_blocked_by_existing_sleep_rule_shape(self):
        ok, _ = should_block_proactive({"activity": "睡觉", "energy": 20}, kind="greeting_evening")
        self.assertFalse(ok)

    def test_urgent_user_text_bypasses_passive_delay(self):
        self.assertFalse(should_delay_passive_reply("上课", "救命 出事了"))
        self.assertTrue(should_delay_passive_reply("上课", "在干嘛"))

    def test_greeting_also_blocked_while_busy(self):
        ok, reason = should_block_proactive({"activity": "上课", "energy": 80}, kind="greeting_morning")
        self.assertTrue(ok)
        self.assertIn("忙", reason)
        self.assertIn("上课", reason)

        ok, reason = should_block_proactive({"activity": "上课", "energy": 80}, kind="greeting_evening")
        self.assertTrue(ok)
        self.assertIn("忙", reason)

    def test_nap_does_not_delay_passive(self):
        self.assertFalse(should_delay_passive_reply("午睡", "在干嘛"))
        self.assertFalse(should_delay_passive_reply("摸鱼", "你好"))


if __name__ == "__main__":
    unittest.main()
