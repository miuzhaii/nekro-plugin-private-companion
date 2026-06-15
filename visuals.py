"""视觉资产与日程自拍支持。

设计原则：
- 图片文件保存在插件数据目录下（persona_assets / schedule_selfies）。
- plugin.store 只保存小状态/索引；图片元数据放 JSON sidecar。
- 本模块的纯逻辑函数不依赖 Nekro 运行时，便于单元测试。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, List
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_REFERENCE_IMAGE = "persona_assets/persona.png"
DEFAULT_NEGATIVE_PROMPT = "low quality, bad hands, extra fingers, blurry, watermark, text"
DEFAULT_STYLE_PROMPT = "anime illustration, soft light, casual selfie, coherent character design"
PERSONA_REFERENCE_DESCRIPTION = (
    "人设参考图：请严格参考这张图的同一角色外貌、发型、发色、眼睛、服装主色、配色、画风和整体气质；"
    "只改变当前日程对应的姿势、表情、场景与光线，不要重设计角色，不要换画风。"
)
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# 覆盖日程活动里常见中文，避免没有 pypinyin 依赖时 slug 全丢失。
_PINYIN_HINTS = {
    "窝": "wo", "在": "zai", "沙": "sha", "发": "fa", "上": "shang", "看": "kan", "视": "shi", "频": "pin",
    "刷": "shua", "手": "shou", "机": "ji", "读": "du", "书": "shu", "午": "wu", "饭": "fan", "休": "xiu",
    "睡": "shui", "觉": "jiao", "醒": "xing", "来": "lai", "早": "zao", "晚": "wan", "夜": "ye", "客": "ke",
    "厅": "ting", "卧": "wo", "室": "shi", "吃": "chi", "喝": "he", "茶": "cha", "咖": "ka", "啡": "fei",
    "画": "hua", "图": "tu", "写": "xie", "代": "dai", "码": "ma", "游": "you", "戏": "xi", "散": "san", "步": "bu",
    "洗": "xi", "漱": "shu", "床": "chuang", "宅": "zhai", "家": "jia", "摸": "mo", "鱼": "yu", "音": "yin",
    "乐": "yue", "影": "ying", "片": "pian", "整": "zheng", "理": "li", "房": "fang", "间": "jian", "工": "gong",
    "作": "zuo", "学": "xue", "习": "xi", "出": "chu", "门": "men", "买": "mai", "饮": "yin", "料": "liao",
}


@dataclass
class PersonaVisualProfile:
    character_prompt: str = ""
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    style_prompt: str = DEFAULT_STYLE_PROMPT
    reference_image: str = DEFAULT_REFERENCE_IMAGE
    image_sha256: str = ""
    seed_hint: str = ""
    updated_at: float = 0.0
    version: int = 1
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PersonaVisualProfile":
        if not isinstance(data, dict):
            data = {}
        known = {
            "character_prompt": str(data.get("character_prompt") or ""),
            "negative_prompt": str(data.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT),
            "style_prompt": str(data.get("style_prompt") or DEFAULT_STYLE_PROMPT),
            "reference_image": str(data.get("reference_image") or DEFAULT_REFERENCE_IMAGE),
            "image_sha256": str(data.get("image_sha256") or ""),
            "seed_hint": str(data.get("seed_hint") or ""),
            "updated_at": float(data.get("updated_at") or 0.0),
            "version": int(data.get("version") or 1),
        }
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(**known, extra=extra)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        extra = data.pop("extra", {}) or {}
        data.update(extra)
        return data


def ensure_visual_dirs(base_dir: Path | str) -> Path:
    base = Path(base_dir)
    (base / "persona_assets").mkdir(parents=True, exist_ok=True)
    (base / "schedule_selfies").mkdir(parents=True, exist_ok=True)
    return base


def persona_profile_path(base_dir: Path | str) -> Path:
    return ensure_visual_dirs(base_dir) / "persona_assets" / "persona.json"


def load_persona_visual_profile(base_dir: Path | str) -> PersonaVisualProfile:
    path = persona_profile_path(base_dir)
    if not path.exists():
        return PersonaVisualProfile()
    try:
        return PersonaVisualProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return PersonaVisualProfile()


def save_persona_visual_profile(base_dir: Path | str, profile: PersonaVisualProfile) -> Path:
    ensure_visual_dirs(base_dir)
    if not profile.updated_at:
        profile.updated_at = time.time()
    path = persona_profile_path(base_dir)
    path.write_text(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_persona_image_bytes(base_dir: Path | str, content: bytes, filename: str = "persona.png") -> PersonaVisualProfile:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("PERSONA_IMAGE_TOO_LARGE")
    suffix = Path(filename or "persona.png").suffix.lower() or ".png"
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("UNSUPPORTED_PERSONA_IMAGE_TYPE")
    ensure_visual_dirs(base_dir)
    out_name = f"persona{suffix}"
    out = Path(base_dir) / "persona_assets" / out_name
    out.write_bytes(content)
    profile = load_persona_visual_profile(base_dir)
    profile.reference_image = f"persona_assets/{out_name}"
    profile.image_sha256 = sha256_bytes(content)
    profile.updated_at = time.time()
    save_persona_visual_profile(base_dir, profile)
    return profile


def _slugify_text(text: str, max_parts: int = 16) -> str:
    raw = " ".join(str(text or "").split()).lower()
    parts = []
    for ch in raw:
        if ch.isascii() and ch.isalnum():
            parts.append(ch)
        elif ch in _PINYIN_HINTS:
            parts.append("_" + _PINYIN_HINTS[ch] + "_")
        elif ch in {" ", "_", "-", "/", "，", ",", "。", ".", "、", "|"}:
            parts.append("_")
        else:
            normalized = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii")
            if normalized:
                parts.append(normalized)
            else:
                parts.append("_")
    slug = re.sub(r"_+", "_", "".join(parts)).strip("_")
    tokens = [x for x in slug.split("_") if x]
    return "_".join(tokens[:max_parts]) or "event"


def _window_start_hhmm(window: str) -> str:
    m = re.search(r"(\d{1,2})[:：](\d{2})", str(window or ""))
    if not m:
        return "0000"
    return f"{int(m.group(1)) % 24:02d}{int(m.group(2)) % 60:02d}"


def schedule_selfie_relative_path(event: Dict[str, Any], date_key: str) -> Path:
    hhmm = _window_start_hhmm(str(event.get("window") or event.get("time") or ""))
    slug = _slugify_text(str(event.get("activity") or event.get("event") or "event"))
    return Path("schedule_selfies") / str(date_key) / f"{hhmm}_{slug}.png"


def cache_meta_path_for_image(image_rel_path: Path | str) -> Path:
    return Path(image_rel_path).with_suffix(".json")


def is_safe_relative_image_path(rel_path: str | Path) -> bool:
    p = Path(str(rel_path))
    if p.is_absolute() or any(part == ".." for part in p.parts):
        return False
    if p.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        return False
    return bool(p.parts) and p.parts[0] in {"persona_assets", "schedule_selfies"}


def resolve_safe_image_path(base_dir: Path | str, rel_path: str | Path) -> Path:
    if not is_safe_relative_image_path(rel_path):
        raise ValueError("UNSAFE_IMAGE_PATH")
    base = ensure_visual_dirs(base_dir).resolve()
    target = (base / Path(rel_path)).resolve()
    if base != target and base not in target.parents:
        raise ValueError("UNSAFE_IMAGE_PATH")
    return target


def _condition_text(state: Dict[str, Any]) -> str:
    names = []
    for item in state.get("conditions") or []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("desc") or item.get("type") or "").strip()
            strength = item.get("strength")
            if name:
                names.append(f"{name}{f'({strength})' if strength is not None else ''}")
        elif str(item).strip():
            names.append(str(item).strip())
    return "、".join(names)


def _scene_time_hint(hhmm: str) -> str:
    try:
        hour = int(str(hhmm).split(":", 1)[0])
    except Exception:
        hour = 12
    if 5 <= hour < 9:
        return "清晨室内"
    if 9 <= hour < 12:
        return "上午自然光"
    if 12 <= hour < 14:
        return "午后室内"
    if 14 <= hour < 18:
        return "下午柔光"
    if 18 <= hour < 21:
        return "傍晚室内暖光"
    return "夜晚室内"


def build_selfie_prompt(
    event: Dict[str, Any],
    state: Dict[str, Any],
    persona: PersonaVisualProfile,
    hhmm: str = "",
) -> str:
    activity = " ".join(str(event.get("activity") or event.get("event") or "日常休息").split())
    mood = " ".join(str(event.get("mood") or state.get("mood") or "平静").split())
    hhmm = hhmm or _window_start_hhmm(str(event.get("window") or ""))[:2] + ":00"
    scene = _scene_time_hint(hhmm)
    conditions = _condition_text(state)
    energy = state.get("energy", "")
    parts = [
        persona.character_prompt.strip() or "日常陪伴角色，真实自然的生活感",
        persona.style_prompt.strip() or DEFAULT_STYLE_PROMPT,
        f"casual selfie, {scene}, 时间 {hhmm}",
        f"当前活动：{activity}",
        f"当前心情：{mood}",
    ]
    if energy != "":
        parts.append(f"精神状态/能量：{energy}/100")
    if conditions:
        parts.append(f"身体小状态：{conditions}")
    parts.extend([
        "画面像随手发给亲近的人看的生活自拍，有轻微生活杂物但不凌乱",
        "必须保持同一角色外貌、服装配色和画风一致，构图自然，表情真实，不要摆拍感太强",
    ])
    if persona.image_sha256:
        parts.append(f"角色一致性锚点/参考图sha256：{persona.image_sha256}")
    if persona.seed_hint.strip():
        parts.append(f"一致性提示/seed：{persona.seed_hint.strip()}")
    return "，".join(p for p in parts if p)


def get_persona_reference_for_generation(base_dir: Path | str, persona: PersonaVisualProfile) -> Optional[tuple[Path, str]]:
    """返回可用于图生图/参考图生成的人设图绝对路径与描述。"""
    ref = str(persona.reference_image or "").strip()
    if not ref:
        return None
    try:
        path = resolve_safe_image_path(base_dir, ref)
    except ValueError:
        return None
    if not path.exists() or not path.is_file():
        return None
    return path, PERSONA_REFERENCE_DESCRIPTION


def build_selfie_capability_prompt() -> str:
    """给对话模型注入日程自拍工具的使用说明。"""
    return (
        "\n【日程自拍能力】\n"
        "你可以在合适时调用 `send_current_schedule_selfie(chat_key, force=False)` 把当前日程自拍发到当前聊天。\n"
        "适合调用的场景：用户问你当前在做什么/现在在做什么、想看看你、要照片/自拍/日程图，"
        "或聊天自然提到你当前活动并适合配一张图时。\n"
        "调用时 `chat_key` 使用当前会话的 chat_key；一般不要 force，除非用户明确说重拍/刷新。\n"
        "工具会优先发送已缓存的当前日程图；没有缓存时才按额度生成。"
        "如果工具返回失败原因，要如实说明，不要编造已经发送。"
    )


def decode_image_result_to_bytes(result: str) -> tuple[bytes, str]:
    """把图片服务返回的 data URL/base64/http URL 转为 bytes 和后缀。"""
    text = str(result or "").strip()
    if not text:
        raise ValueError("EMPTY_IMAGE_RESULT")
    if text.startswith("data:image"):
        header, b64 = text.split(",", 1)
        mime = header.split(";", 1)[0].split(":", 1)[1]
        suffix = ".jpg" if "jpeg" in mime or "jpg" in mime else ".webp" if "webp" in mime else ".png"
        return base64.b64decode(b64), suffix
    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        suffix = Path(parsed.path).suffix.lower()
        if suffix not in ALLOWED_IMAGE_SUFFIXES:
            suffix = ".png"
        req = Request(text, headers={"User-Agent": "Nekro-private-companion/1.0"})
        with urlopen(req, timeout=60) as resp:  # noqa: S310 - user-configured image provider URL
            return resp.read(MAX_UPLOAD_BYTES + 1), suffix
    # 尝试纯 base64
    return base64.b64decode(text), ".png"


async def generate_selfie_image_with_retries(
    prompt: str,
    generator: Callable[..., Any],
    reference_image: Optional[tuple[Path, str]] = None,
    retries: int = 1,
    retry_delay: float = 1.5,
) -> Any:
    """调用图片生成器，失败后重试 retries 次，最终抛出最后一次错误。

    注意：如果存在可用人设参考图，必须把 reference_image 传给生成器。
    不能因旧签名 TypeError 静默降级为纯文生图，否则 sidecar 会显示 used_reference_image=true，
    但实际生成没有吃到人设图，导致日程图角色和风格漂移。
    """
    attempts = max(0, int(retries)) + 1
    last_error: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            if reference_image is not None:
                result = generator(prompt, reference_image=reference_image)
            else:
                result = generator(prompt)
            if hasattr(result, "__await__"):
                result = await result
            return result
        except BaseException as e:  # 保留最后错误给调用方展示
            last_error = e
            if attempt >= attempts - 1:
                break
            if retry_delay > 0:
                await asyncio.sleep(retry_delay)
    assert last_error is not None
    raise last_error


async def get_or_generate_schedule_selfie(
    base_dir: Path | str,
    event: Dict[str, Any],
    state: Dict[str, Any],
    persona: PersonaVisualProfile,
    date_key: str,
    generator: Callable[[str], Any],
    hhmm: str = "",
    force: bool = False,
    retries: int = 1,
    retry_delay: float = 1.5,
) -> Path:
    """按日程事件读取或生成自拍图。

    generator 接收 prompt，返回 data URL/base64/http URL 或 bytes。
    """
    ensure_visual_dirs(base_dir)
    rel = schedule_selfie_relative_path(event, date_key)
    out = Path(base_dir) / rel
    meta = Path(base_dir) / cache_meta_path_for_image(rel)
    if out.exists() and not force:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_selfie_prompt(event, state, persona, hhmm=hhmm)
    reference_image = get_persona_reference_for_generation(base_dir, persona)
    result = await generate_selfie_image_with_retries(
        prompt,
        generator,
        reference_image=reference_image,
        retries=retries,
        retry_delay=retry_delay,
    )
    if isinstance(result, bytes):
        content = result
    else:
        content, _suffix = decode_image_result_to_bytes(str(result))
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("GENERATED_IMAGE_TOO_LARGE")
    out.write_bytes(content)
    meta.write_text(
        json.dumps(
            {
                "event": event,
                "prompt": prompt,
                "negative_prompt": persona.negative_prompt,
                "style_prompt": persona.style_prompt,
                "reference_image": reference_image[0].relative_to(Path(base_dir)).as_posix() if reference_image else "",
                "reference_description": reference_image[1] if reference_image else "",
                "used_reference_image": bool(reference_image),
                "created_at": time.time(),
                "source_persona_sha256": persona.image_sha256,
                "image_sha256": sha256_bytes(content),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def _event_status_item(base_dir: Path | str, event: Dict[str, Any], date_key: str, index: int) -> Dict[str, Any]:
    rel = schedule_selfie_relative_path(event, date_key)
    image_path = Path(base_dir) / rel
    meta_rel = cache_meta_path_for_image(rel)
    meta_path = Path(base_dir) / meta_rel
    return {
        "index": index,
        "window": str(event.get("window") or ""),
        "activity": str(event.get("activity") or event.get("event") or ""),
        "mood": str(event.get("mood") or ""),
        "image": rel.as_posix(),
        "meta": meta_rel.as_posix(),
        "exists": image_path.exists(),
        "meta_exists": meta_path.exists(),
        "event": event,
    }


def list_daily_schedule_selfie_status(base_dir: Path | str, events: List[Dict[str, Any]], date_key: str) -> List[Dict[str, Any]]:
    """列出今日每个日程时间段的自拍缓存状态。"""
    ensure_visual_dirs(base_dir)
    items: List[Dict[str, Any]] = []
    for i, ev in enumerate(events or []):
        if isinstance(ev, dict):
            items.append(_event_status_item(base_dir, ev, date_key, i))
    return items


async def get_or_generate_daily_schedule_selfies(
    base_dir: Path | str,
    events: List[Dict[str, Any]],
    state: Dict[str, Any],
    persona: PersonaVisualProfile,
    date_key: str,
    generator: Callable[[str], Any],
    force: bool = False,
    retries: int = 1,
    retry_delay: float = 1.5,
    limit: int = 24,
) -> List[Dict[str, Any]]:
    """为今日所有日程事件逐个生成/读取自拍。返回每个时间段的状态。"""
    ensure_visual_dirs(base_dir)
    clean_events = [ev for ev in (events or []) if isinstance(ev, dict)]
    if limit and limit > 0:
        clean_events = clean_events[:limit]
    results: List[Dict[str, Any]] = []
    for i, ev in enumerate(clean_events):
        image_path = await get_or_generate_schedule_selfie(
            base_dir,
            ev,
            state,
            persona,
            date_key=date_key,
            generator=generator,
            hhmm=str(ev.get("window") or "")[:5],
            force=force,
            retries=retries,
            retry_delay=retry_delay,
        )
        item = _event_status_item(base_dir, ev, date_key, i)
        item["image"] = image_path.relative_to(Path(base_dir)).as_posix()
        item["exists"] = True
        results.append(item)
    return results

