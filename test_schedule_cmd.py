# -*- coding: utf-8 -*-
"""Source-scan tests for /陪伴 日程生成 wiring. Do not import handlers/plugin/on_command."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class TestScheduleGenerateCommandSource(unittest.TestCase):
    def test_handlers_wires_schedule_generate_and_card_render(self):
        src = ROOT.joinpath("handlers.py").read_text(encoding="utf-8")
        self.assertIn("日程生成", src)
        self.assertIn("render_schedule_card", src)
        self.assertIn("qq_avatar_url", src)
        self.assertIn("self_id", src)
        self.assertTrue(
            "format_user_error('日程生成失败'" in src
            or 'format_user_error("日程生成失败"' in src,
            "handlers.py must call format_user_error for 日程生成失败",
        )
        self.assertRegex(
            src,
            r'action == ["\']日程生成["\']',
            "must parse action == 日程生成",
        )
        self.assertIn("parts[1] == \"生成\"", src)
        help_m = re.search(r"HELP_TEXT\s*=\s*\"\"\"(.*?)\"\"\"", src, re.S)
        self.assertIsNotNone(help_m, "HELP_TEXT not found")
        assert help_m is not None
        self.assertIn("日程生成", help_m.group(1))

    def test_plugin_has_schedule_card_config(self):
        src = ROOT.joinpath("plugin.py").read_text(encoding="utf-8")
        self.assertIn("SCHEDULE_CARD_ENABLED", src)
        self.assertIn("RENDERER_URL", src)


if __name__ == "__main__":
    unittest.main()
