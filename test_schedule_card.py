# -*- coding: utf-8 -*-
"""TDD tests for schedule-card HTML + Pillow renderer (pure functions)."""
from __future__ import annotations

import re
import unittest
from types import SimpleNamespace

from schedule_card import (
    DEFAULT_VIEWPORT_WIDTH,
    build_schedule_html,
    encode_avatar_data_uri,
    escape_html,
    event_tone,
    html_to_image,
    qq_avatar_url,
    render_fallback_png,
    render_schedule_card,
)

IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
HTTP_RE = re.compile(r"https?://", re.IGNORECASE)
PNG_MAGIC = b"\x89PNG"

FAKE_EVENTS = [
    {"window": "09:00-12:00", "activity": "图书馆自习", "mood": "专注"},
    {"window": "14:00-17:00", "activity": "社团排练", "mood": "兴奋"},
]
FAKE_DAY_CARD = {
    "scene": {"title": "下雨困在家", "setting": "一整天都出不去"},
    "events": [{"blurb": "快递丢了"}, {"blurb": "突然停电"}],
}


def _tiny_png() -> bytes:
    return render_fallback_png(
        date_key="x",
        summary="y",
        events=[{"window": "08:00-09:00", "activity": "起床", "mood": ""}],
        day_card=None,
    )


def _strip_data_uris(html: str) -> str:
    return re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "", html)


def _sample_html(**overrides) -> str:
    kwargs = dict(
        date_key="2026-08-30",
        summary="今天窝在家里把作业清掉。",
        events=FAKE_EVENTS,
        day_card=FAKE_DAY_CARD,
        current_window="",
        avatar_data_uri="",
        avatar_url="",
        bot_name="",
    )
    kwargs.update(overrides)
    return build_schedule_html(**kwargs)


class TestEscapeHtml(unittest.TestCase):
    def test_escape_html_escapes_angle_brackets(self):
        self.assertEqual(escape_html("<script>"), "&lt;script&gt;")


class TestEventTone(unittest.TestCase):
    def test_study_and_rehearsal_use_different_colors(self):
        a = event_tone("图书馆自习", 0)
        b = event_tone("社团排练", 1)
        self.assertNotEqual(a["main"], b["main"])
        self.assertTrue(a["main"].startswith("#"))
        self.assertTrue(b["light"].startswith("#"))

    def test_sleep_keyword_is_stable(self):
        self.assertEqual(event_tone("睡觉", 0)["main"], event_tone("午睡回笼", 9)["main"])


class TestEncodeAvatar(unittest.TestCase):
    def test_data_uri_has_no_http_or_ipv4(self):
        uri = encode_avatar_data_uri(_tiny_png())
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        self.assertIsNone(HTTP_RE.search(uri))
        self.assertIsNone(IPV4_RE.search(_strip_data_uris(uri)))

    def test_qq_avatar_url_official_qlogo(self):
        url = qq_avatar_url("12435768")
        self.assertEqual(url, "https://q1.qlogo.cn/g?b=qq&nk=12435768&s=640")
        self.assertEqual(qq_avatar_url(""), "")
        self.assertEqual(qq_avatar_url("abc"), "")


class TestBuildScheduleHtml(unittest.TestCase):
    def test_includes_date_summary_activity_window_scene_and_blurbs(self):
        html = _sample_html()
        self.assertIn("今日日程", html)
        self.assertIn("2026-08-30", html)
        self.assertIn("今天窝在家里把作业清掉。", html)
        self.assertIn("图书馆自习", html)
        self.assertIn("09:00-12:00", html)
        self.assertIn("下雨困在家", html)
        self.assertIn("快递丢了", html)
        self.assertIn("突然停电", html)
        self.assertIn("专注", html)

    def test_html_contains_no_http_https_or_ipv4(self):
        html = _sample_html()
        self.assertIsNone(HTTP_RE.search(html), html)
        self.assertIsNone(IPV4_RE.search(html), html)

    def test_escapes_script_in_activity(self):
        html = _sample_html(
            events=[
                {
                    "window": "09:00-12:00",
                    "activity": "<script>alert(1)</script>",
                    "mood": "ok",
                }
            ],
            day_card=None,
        )
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_current_window_event_is_marked(self):
        html = _sample_html(current_window="14:00-17:00")
        self.assertTrue(
            'class="event current"' in html
            or "class='event current'" in html
            or 'class="event current ' in html
            or "data-current" in html
            or "current-window" in html
            or "is-current" in html,
            "current_window event should carry a highlight class or marker",
        )
        self.assertIn("社团排练", html)

    def test_viewport_width_constant(self):
        self.assertEqual(DEFAULT_VIEWPORT_WIDTH, 560)
        html = _sample_html()
        self.assertIn("560", html)

    def test_timeline_and_colored_events(self):
        html = _sample_html()
        self.assertIn("class='timeline'", html)
        self.assertIn("class='dot'", html)
        self.assertIn("class='rail'", html)
        self.assertIn("--tone:", html)

    def test_avatar_uses_official_qq_qlogo(self):
        html = _sample_html(avatar_url="https://q1.qlogo.cn/g?b=qq&nk=12435768&s=640", bot_name="陪伴")
        self.assertIn("q1.qlogo.cn", html)
        self.assertIn("nk=12435768", html)
        self.assertIn("陪伴", html)
        stripped = html.replace("https://q1.qlogo.cn/g?b=qq&amp;nk=12435768&amp;s=640", "")
        stripped = stripped.replace("https://q1.qlogo.cn/g?b=qq&nk=12435768&s=640", "")
        self.assertIsNone(HTTP_RE.search(stripped), stripped)
        self.assertIsNone(IPV4_RE.search(html), html)

    def test_avatar_rejects_non_qlogo_http(self):
        html = _sample_html(avatar_url="http://evil.example/x.png")
        self.assertNotIn("evil.example", html)
        self.assertNotIn("http://", html)


class TestRenderFallbackPng(unittest.TestCase):
    def test_fallback_png_starts_with_magic(self):
        data = render_fallback_png(
            date_key="2026-08-30",
            summary="今天窝在家里把作业清掉。",
            events=FAKE_EVENTS,
            day_card=FAKE_DAY_CARD,
            avatar_bytes=_tiny_png(),
        )
        self.assertTrue(data.startswith(PNG_MAGIC))
        self.assertGreater(len(data), 64)


class TestHtmlToImage(unittest.IsolatedAsyncioTestCase):
    async def test_injected_post_hits_screenshot_and_returns_png(self):
        captured = {}

        async def fake_post(url, json, timeout):
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout
            return SimpleNamespace(
                content=PNG_MAGIC + b"fake",
                raise_for_status=lambda: None,
            )

        out = await html_to_image(
            "<html></html>",
            renderer_url="http://127.0.0.1:3000/",
            timeout=12,
            post=fake_post,
        )
        self.assertTrue(captured["url"].endswith("/screenshot"))
        self.assertEqual(out, PNG_MAGIC + b"fake")
        self.assertEqual(captured["json"]["options"]["fullPage"], True)
        self.assertEqual(captured["json"]["options"]["type"], "png")
        self.assertEqual(captured["json"]["viewport"]["width"], 560)

    async def test_invalid_bytes_raise_runtimeerror_without_url(self):
        leak_url = "http://10.0.0.9:3000"

        async def fake_post(url, json, timeout):
            return SimpleNamespace(
                content=b"not-a-png",
                raise_for_status=lambda: None,
            )

        with self.assertRaises(RuntimeError) as ctx:
            await html_to_image(
                "<html></html>",
                renderer_url=leak_url,
                post=fake_post,
            )
        msg = str(ctx.exception)
        self.assertIn("渲染返回无效图片", msg)
        self.assertNotIn(leak_url, msg)
        self.assertIsNone(HTTP_RE.search(msg))
        self.assertIsNone(IPV4_RE.search(msg))


class TestRenderScheduleCard(unittest.IsolatedAsyncioTestCase):
    async def test_empty_renderer_url_returns_fallback_png(self):
        data = await render_schedule_card(
            date_key="2026-08-30",
            summary="今天窝在家里把作业清掉。",
            events=FAKE_EVENTS,
            day_card=FAKE_DAY_CARD,
            renderer_url="",
        )
        self.assertTrue(data.startswith(PNG_MAGIC))

    async def test_post_that_raises_still_returns_fallback_png(self):
        async def boom(url, json, timeout):
            raise RuntimeError("renderer down at http://10.1.2.3:3000")

        data = await render_schedule_card(
            date_key="2026-08-30",
            summary="今天窝在家里把作业清掉。",
            events=FAKE_EVENTS,
            day_card=FAKE_DAY_CARD,
            renderer_url="http://10.1.2.3:3000",
            post=boom,
        )
        self.assertTrue(data.startswith(PNG_MAGIC))


if __name__ == "__main__":
    unittest.main()
