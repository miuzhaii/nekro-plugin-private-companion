"""日程自拍出图：走 Nekro 已配置的绘图模型组，不依赖第三方绘图插件。"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Sequence, Tuple

import httpx
from httpx import Timeout

DEFAULT_SYSTEM_PROMPT = (
    "You are a professional painter. Use your high-quality drawing skills to draw a "
    "picture based on the user's description. Just provide the image and do not ask "
    "for more information."
)


def _normalize_base_url(url: str) -> str:
    return str(url or "").rstrip("/")


def build_chat_image_messages(
    prompt: str,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    reference_images: Optional[Sequence[Tuple[str, str]]] = None,
    use_system_role: bool = False,
) -> List[dict]:
    """构造 OpenAI 兼容多模态 chat 消息（参考图 + 提示词）。"""
    user_content: List[dict] = []
    for img_data, img_desc in reference_images or []:
        if not img_data:
            continue
        user_content.append({"type": "image_url", "image_url": {"url": img_data}})
        if img_desc:
            user_content.append({"type": "text", "text": f"{img_desc}\n"})

    if not use_system_role and system_prompt:
        full_prompt = f"{system_prompt}\n\n{prompt}"
    else:
        full_prompt = prompt
    user_content.append({"type": "text", "text": full_prompt})

    messages: List[dict] = []
    if use_system_role and system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_content})
    return messages


def _image_from_delta_or_message(node: Any) -> Optional[str]:
    if not isinstance(node, dict):
        return None
    image_data = node.get("image")
    if image_data and isinstance(image_data, list) and image_data:
        first = image_data[0]
        if isinstance(first, dict):
            data = first.get("data") or first.get("b64_json") or first.get("url")
        else:
            data = first
        if isinstance(data, str) and data.strip():
            data = data.strip()
            if data.startswith(("http://", "https://", "data:image")):
                return data
            return f"data:image/png;base64,{data}"
    return None


def extract_image_from_chat_payload(payload: dict, collected_content: str = "") -> Optional[str]:
    """从流式 delta 或非流式 message 中提取图片 URL / data URL。"""
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices, list):
        return None
    first = choices[0] if isinstance(choices[0], dict) else {}
    for key in ("delta", "message"):
        found = _image_from_delta_or_message(first.get(key) or {})
        if found:
            return found
        content = (first.get(key) or {}).get("content") if isinstance(first.get(key), dict) else None
        if isinstance(content, str) and content:
            collected_content += content
    return extract_image_from_text(collected_content)


def extract_image_from_text(content: str) -> Optional[str]:
    text = str(content or "").strip()
    if not text:
        return None
    match = re.search(r"!\[.*?\]\((.*?)\)", text)
    if match:
        return match.group(1)
    if text.startswith(("http://", "https://", "data:image")):
        return text
    if re.match(r"^[A-Za-z0-9+/=\s]+$", text) and len(text) > 80:
        compact = re.sub(r"\s+", "", text)
        return f"data:image/png;base64,{compact}"
    return None


async def generate_image_via_chat(
    model_group: Any,
    prompt: str,
    timeout: float = 300.0,
    system_prompt: Optional[str] = None,
    use_system_role: bool = False,
    reference_images: Optional[Sequence[Tuple[str, str]]] = None,
    stream_mode: bool = True,
    client: Any = None,
) -> str:
    """用绘图模型组的 chat/completions 出图。client 仅供测试注入。"""
    if system_prompt is None:
        system_prompt = DEFAULT_SYSTEM_PROMPT
    messages = build_chat_image_messages(
        prompt,
        system_prompt=system_prompt,
        reference_images=reference_images,
        use_system_role=use_system_role,
    )
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {getattr(model_group, 'API_KEY', '')}",
    }
    json_data = {
        "model": getattr(model_group, "CHAT_MODEL", ""),
        "messages": messages,
        "stream": stream_mode,
    }
    url = f"{_normalize_base_url(getattr(model_group, 'BASE_URL', ''))}/chat/completions"
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=Timeout(read=timeout, write=timeout, connect=10, pool=10))

    collected_content = ""
    collected_image: Optional[str] = None
    try:
        if owns_client:
            async with client:
                collected_image, collected_content = await _request_chat(
                    client, url, headers, json_data, stream_mode
                )
        else:
            collected_image, collected_content = await _request_chat(
                client, url, headers, json_data, stream_mode
            )
    except TypeError:
        # 测试用假 client 可能不是 async context manager
        collected_image, collected_content = await _request_chat(
            client, url, headers, json_data, stream_mode
        )

    if collected_image:
        return collected_image
    found = extract_image_from_text(collected_content)
    if found:
        return found
    raise ValueError("未能从模型响应中提取图片，请检查模型组是否支持图像生成")


async def _request_chat(client, url, headers, json_data, stream_mode: bool) -> tuple[Optional[str], str]:
    collected_image: Optional[str] = None
    collected_content = ""
    if stream_mode:
        async with client.stream("POST", url, headers=headers, json=json_data) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                line = (line or "").strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    found = extract_image_from_chat_payload(chunk)
                    if found and found.startswith("data:image"):
                        collected_image = found
                    elif found and not collected_image:
                        collected_image = found
                    choices = chunk.get("choices") or []
                    if choices and isinstance(choices[0], dict):
                        delta = choices[0].get("delta") or {}
                        content_data = delta.get("content")
                        if content_data:
                            collected_content += content_data
        return collected_image, collected_content

    response = await client.post(url, headers=headers, json=json_data)
    response.raise_for_status()
    data = response.json()
    collected_image = extract_image_from_chat_payload(data)
    choices = data.get("choices") or []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        content_data = message.get("content")
        if content_data:
            collected_content = content_data
    return collected_image, collected_content
