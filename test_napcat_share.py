# -*- coding: utf-8 -*-
"""TDD: map host plugin-data paths to NapCat-visible file URIs."""
from __future__ import annotations

import unittest
from pathlib import Path

from napcat_share import napcat_file_uri
from proactive_queue import format_user_error, redact_error


class TestNapcatFileUri(unittest.TestCase):
    def test_maps_host_data_dir_to_napcat_share(self):
        host = Path("/root/srv/nekro_agent/plugin_data/xiaojiu.private_companion/schedule_cards/schedule_2026-08-30.png")
        uri = napcat_file_uri(host)
        self.assertEqual(
            uri,
            "file:///app/nekro_agent_data/plugin_data/xiaojiu.private_companion/schedule_cards/schedule_2026-08-30.png",
        )
        self.assertNotIn("/root/srv", uri)

    def test_passthrough_when_outside_data_dir(self):
        uri = napcat_file_uri("/tmp/not-in-data.png")
        self.assertTrue(uri.startswith("file://"))
        self.assertIn("not-in-data.png", uri)


class TestRedactHostPath(unittest.TestCase):
    def test_redacts_host_path_and_file_uri(self):
        raw = (
            "ENOENT: no such file or directory, open "
            "'/root/srv/nekro_agent/plugin_data/xiaojiu.private_companion/schedule_cards/x.png'"
        )
        out = redact_error(raw)
        self.assertNotIn("/root/", out)
        self.assertNotIn("nekro_agent", out)
        self.assertIn("「[路径已隐藏]」", out)

    def test_format_user_error_hides_enoent_path(self):
        exc = Exception(
            "ActionFailed message=\"ENOENT: no such file or directory, open "
            "'/root/srv/nekro_agent/plugin_data/x.png'\""
        )
        msg = format_user_error("日程生成失败", exc)
        self.assertNotIn("/root/", msg)
        self.assertNotIn("http", msg)
        self.assertIn("日程生成失败", msg)


class TestHandlersUseNapcatShare(unittest.TestCase):
    def test_send_local_image_uses_napcat_file_uri(self):
        src = Path(__file__).resolve().parent.joinpath("handlers.py").read_text(encoding="utf-8")
        self.assertIn("napcat_file_uri", src)
        self.assertIn("_send_local_image", src)


if __name__ == "__main__":
    unittest.main()
