# -*- coding: utf-8 -*-
"""Queue retry must re-run should_send before trigger_proactive."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from proactive_queue import decide_queue_retry, enqueue


class TestDecideQueueRetry(unittest.TestCase):
    def test_send_when_should_send_ok(self):
        item = {"user_id": "1", "kind": "nudge", "motivation": {"kind": "nudge"}}
        self.assertEqual(decide_queue_retry(True, item), "send")

    def test_requeue_when_should_send_blocks(self):
        item = {"user_id": "1", "kind": "nudge", "motivation": {"kind": "nudge"}}
        self.assertEqual(decide_queue_retry(False, item), "requeue")

    def test_requeue_preserves_kind_and_motivation_payload(self):
        item = {
            "user_id": "12435768",
            "kind": "share_activity",
            "motivation": {"kind": "share_activity", "desc": "补发"},
        }
        self.assertEqual(decide_queue_retry(False, item), "requeue")
        q = []
        enqueue(
            q,
            {
                "user_id": item["user_id"],
                "kind": item["kind"],
                "motivation": item["motivation"],
                "error": "正忙着上课，先不主动打扰",
            },
            now=10.0,
        )
        self.assertEqual(q[0]["kind"], "share_activity")
        self.assertEqual(q[0]["motivation"]["kind"], "share_activity")


class TestSchedulerUsesRetryDecision(unittest.TestCase):
    def test_proactive_scheduler_calls_decide_queue_retry(self):
        src = (Path(__file__).resolve().parent / "proactive.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        found_decide = False
        found_should_send_after_pop = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name == "decide_queue_retry":
                    found_decide = True
                if name == "should_send":
                    found_should_send_after_pop = True
        self.assertTrue(found_decide, "scheduler must call decide_queue_retry")
        self.assertTrue(found_should_send_after_pop, "scheduler must re-run should_send")


if __name__ == "__main__":
    unittest.main()
