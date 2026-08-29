"""日程自拍出图入口：只用 Nekro 已配置的绘图模型组，不依赖第三方绘图插件。"""

from __future__ import annotations

import base64
from pathlib import Path

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
    model_group = _resolve_draw_model_group()
    return await generate_image_via_chat(
        model_group,
        prompt,
        timeout=300,
        reference_images=_reference_images(reference_image),
        stream_mode=True,
    )
