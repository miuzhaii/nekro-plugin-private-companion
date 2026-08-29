"""日程自拍出图入口：只用 Nekro 已配置的绘图模型组，不依赖第三方绘图插件。"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path


def validate_selfie_config(enabled, group_name):
    if not enabled:
        return None
    if not str(group_name or "").strip():
        return "启用日程自拍时必须配置绘图模型组 SELFIE_MODEL_GROUP"
    return None


def is_rate_limit_error(exc) -> bool:
    code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if code == 429:
        return True
    resp = getattr(exc, "response", None)
    if getattr(resp, "status_code", None) == 429:
        return True
    return "429" in str(exc)


from nekro_agent.core.config import config as global_config

from .image_gen import generate_image_via_chat
from .plugin import get_config


def _mime_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _reference_images(reference_image: tuple[Path, str] | None):
    if reference_image is None:
        return None
    ref_path, ref_desc = reference_image
    mime = _mime_for_path(ref_path)
    ref_b64 = base64.b64encode(ref_path.read_bytes()).decode("utf-8")
    return [(f"data:{mime};base64,{ref_b64}", ref_desc)]


def _resolve_draw_model_group():
    cfg = get_config()
    group_name = str(cfg.SELFIE_MODEL_GROUP or "").strip()
    if not group_name:
        raise ValueError(
            "未配置自拍图片模型组 SELFIE_MODEL_GROUP。请在插件配置中选择 Nekro 已有的绘图(draw)模型组"
        )
    if group_name not in global_config.MODEL_GROUPS:
        raise ValueError(f"未找到配置的绘图模型组: {group_name}")
    return global_config.MODEL_GROUPS[group_name]


async def generate_image_with_configured_provider(
    prompt: str,
    reference_image: tuple[Path, str] | None = None,
) -> str:
    cfg = get_config()
    model_group = _resolve_draw_model_group()
    retries = max(0, int(getattr(cfg, "SELFIE_RETRIES", 0) or 0))
    delay = float(getattr(cfg, "SELFIE_RETRY_DELAY_SECONDS", 0) or 0)
    last_err: BaseException | None = None
    attempts = retries + 1
    for i in range(attempts):
        try:
            return await generate_image_via_chat(
                model_group,
                prompt,
                timeout=300,
                reference_images=_reference_images(reference_image),
                stream_mode=True,
            )
        except Exception as e:
            last_err = e
            if is_rate_limit_error(e) and i < attempts - 1:
                if delay > 0:
                    await asyncio.sleep(delay)
                continue
            raise
    assert last_err is not None
    raise last_err
