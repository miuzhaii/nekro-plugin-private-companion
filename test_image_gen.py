"""日程自拍出图不得依赖第三方 magic_draw / z_img_draw 插件。"""

from __future__ import annotations

import ast
import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


class TestNoThirdPartyDrawPluginImport(unittest.TestCase):
    def test_plugin_sources_do_not_import_magic_draw(self):
        offenders = []
        for path in sorted(ROOT.glob("*.py")):
            if path.name.startswith("test_"):
                continue
            imported = _imported_modules(path)
            forbidden = (
                "packages.magic_draw",
                "packages.z_img_draw",
            )
            bad = [
                name
                for name in imported
                if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
            ]
            if bad:
                offenders.append(f"{path.name}: {bad}")
        self.assertEqual(
            offenders,
            [],
            "日程自拍不得硬依赖 magic_draw / z_img_draw，应使用本插件内置出图",
        )


class TestChatImageHelpers(unittest.TestCase):
    def test_extract_prefers_image_field_base64(self):
        from image_gen import extract_image_from_chat_payload

        payload = {"choices": [{"delta": {"image": [{"data": "AAA"}]}}]}
        self.assertEqual(extract_image_from_chat_payload(payload), "data:image/png;base64,AAA")

    def test_extract_markdown_url_from_content(self):
        from image_gen import extract_image_from_chat_payload

        payload = {"choices": [{"message": {"content": "ok ![x](https://cdn.example/a.png)"}}]}
        self.assertEqual(extract_image_from_chat_payload(payload), "https://cdn.example/a.png")

    def test_build_messages_puts_reference_image_before_prompt(self):
        from image_gen import build_chat_image_messages

        messages = build_chat_image_messages(
            prompt="画一张自拍",
            system_prompt="You are a painter.",
            reference_images=[("data:image/png;base64,QQQ", "人设参考图")],
            use_system_role=False,
        )
        self.assertEqual(messages[0]["role"], "user")
        content = messages[0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertEqual(content[0]["image_url"]["url"], "data:image/png;base64,QQQ")
        texts = [part["text"] for part in content if part.get("type") == "text"]
        self.assertTrue(any("人设参考图" in t for t in texts))
        self.assertTrue(any("画一张自拍" in t for t in texts))


class _FakeStreamResponse:
    def __init__(self, lines: list[str]):
        self._lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _FakeAsyncClient:
    def __init__(self, lines: list[str]):
        self.lines = lines
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def stream(self, method, url, headers=None, json=None):
        self.calls.append((method, url, headers, json))
        return _FakeStreamResponse(self.lines)


class TestGenerateImageViaChat(unittest.TestCase):
    def test_stream_returns_image_field_without_magic_draw(self):
        from image_gen import generate_image_via_chat

        chunk = {"choices": [{"delta": {"image": [{"data": "QkFTRTY0"}]}}]}
        client = _FakeAsyncClient([f"data: {json.dumps(chunk)}", "data: [DONE]"])
        model = SimpleNamespace(CHAT_MODEL="draw-x", API_KEY="sk-test", BASE_URL="https://llm.example/v1")

        result = asyncio.run(
            generate_image_via_chat(
                model,
                "casual selfie",
                timeout=5,
                reference_images=[("data:image/png;base64,REF", "同一角色")],
                stream_mode=True,
                client=client,
            )
        )
        self.assertEqual(result, "data:image/png;base64,QkFTRTY0")
        self.assertEqual(client.calls[0][1], "https://llm.example/v1/chat/completions")
        sent = client.calls[0][3]
        self.assertEqual(sent["model"], "draw-x")
        self.assertTrue(sent["stream"])
        user_content = sent["messages"][0]["content"]
        self.assertEqual(user_content[0]["image_url"]["url"], "data:image/png;base64,REF")


if __name__ == "__main__":
    unittest.main()
