import unittest
from pathlib import Path

from proactive_queue import enqueue, format_user_error, pop_due, redact_error


USER = "12435768"


class TestPopDueEmpty(unittest.TestCase):
    def test_empty_pop_due_is_none(self):
        self.assertIsNone(pop_due([], 1000.0))


class TestEnqueueDedup(unittest.TestCase):
    def test_two_kinds_then_refresh_same_kind(self):
        q = []
        enqueue(q, {"user_id": USER, "kind": "nudge"}, now=10.0)
        enqueue(q, {"user_id": USER, "kind": "checkin"}, now=11.0)
        self.assertEqual(len(q), 2)

        enqueue(q, {"user_id": USER, "kind": "nudge", "motivation": {"why": "again"}}, now=20.0)
        self.assertEqual(len(q), 2)
        nudged = next(item for item in q if item["kind"] == "nudge")
        self.assertEqual(nudged["ts"], 20.0)
        self.assertEqual(nudged["expire_at"], 20.0 + 3600)
        self.assertEqual(nudged["motivation"], {"why": "again"})


class TestPopDueExpiry(unittest.TestCase):
    def test_expired_dropped_unexpired_returned(self):
        q = []
        enqueue(q, {"user_id": USER, "kind": "old"}, now=1.0, ttl_seconds=10)
        enqueue(q, {"user_id": USER, "kind": "fresh"}, now=100.0, ttl_seconds=50)
        # now=20: old expire_at=11 is past; fresh expire_at=150 is still due
        got = pop_due(q, now=20.0)
        self.assertIsNotNone(got)
        self.assertEqual(got["kind"], "fresh")
        self.assertEqual(len(q), 0)


class TestPerUserCap(unittest.TestCase):
    def test_sixth_distinct_kind_does_not_grow_past_five(self):
        q = []
        for i in range(5):
            enqueue(q, {"user_id": USER, "kind": f"k{i}"}, now=float(i))
        self.assertEqual(len(q), 5)
        enqueue(q, {"user_id": USER, "kind": "k5"}, now=10.0)
        self.assertEqual(len(q), 5)
        kinds = {item["kind"] for item in q}
        self.assertNotIn("k0", kinds)
        self.assertIn("k5", kinds)


class TestRedactError(unittest.TestCase):
    def test_redacts_url_and_ipv4_and_hides_raw(self):
        out = redact_error("fail https://evil.example/x 1.2.3.4")
        self.assertIn("「[链接已隐藏]」", out)
        self.assertIn("「[地址已隐藏]」", out)
        self.assertNotIn("http", out)
        self.assertNotIn("1.2.3.4", out)


class TestFormatUserError(unittest.TestCase):
    def test_user_facing_formatter_does_not_leak_url_or_ip(self):
        msg = format_user_error("自拍失败", Exception("boom https://10.0.0.8:8000/v1/media 10.0.0.8"))
        self.assertNotIn("http", msg)
        self.assertNotIn("10.0.0.8", msg)
        self.assertNotIn("evil.example", msg)
        self.assertIn("自拍失败", msg)
        self.assertIn("「[链接已隐藏]」", msg)
        self.assertIn("「[地址已隐藏]」", msg)

    def test_rate_limit_uses_chinese_retry_copy(self):
        class RateLimit(Exception):
            status_code = 429

        msg = format_user_error("自拍失败", RateLimit("HTTP 429 https://evil.example/x"))
        self.assertEqual(msg, "自拍失败：请求过于频繁，请稍后再试")
        self.assertNotIn("http", msg)
        self.assertNotIn("evil", msg)


class TestEnqueueStoresRedactedError(unittest.TestCase):
    def test_enqueue_stores_redacted_error_never_raw_url(self):
        q = []
        raw = "boom https://evil.example/secret 10.0.0.1"
        enqueue(q, {"user_id": USER, "kind": "fail", "error": raw}, now=1.0)
        self.assertEqual(len(q), 1)
        stored = q[0]["error"]
        self.assertNotIn("http", stored)
        self.assertNotIn("evil.example", stored)
        self.assertNotIn("10.0.0.1", stored)
        self.assertIn("「[链接已隐藏]」", stored)
        self.assertIn("「[地址已隐藏]」", stored)


class TestHandlersUseFormatUserError(unittest.TestCase):
    def test_handlers_source_uses_format_user_error_not_raw_exc(self):
        src = Path(__file__).resolve().parent.joinpath("handlers.py").read_text(encoding="utf-8")
        self.assertIn("format_user_error", src)
        self.assertNotIn('message=f"❌ 自拍失败: {str(e)[:120]}"', src)
        self.assertNotIn('{"success": False, "error": str(e)[:160]}', src)


if __name__ == "__main__":
    unittest.main()
