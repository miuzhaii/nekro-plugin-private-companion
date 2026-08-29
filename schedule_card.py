# -*- coding: utf-8 -*-
"""日程图 HTML + Pillow 纯渲染。无 nekro 依赖。

头像只接受 data URI / 本地字节，禁止把 http/IP 写进卡片。
"""
from __future__ import annotations

import base64
import html as html_lib
import io
import re
from typing import Any, Callable, Optional

DEFAULT_VIEWPORT_WIDTH = 560
_PNG_MAGIC = b"\x89PNG"
QQ_AVATAR_TEMPLATE = "https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"
_QLOGO_RE = re.compile(
    r"^https://q[12]\.qlogo\.cn/g\?b=qq&nk=\d{5,12}&s=(40|100|140|160|640)$"
)

# (keywords, main, light) — first match wins; else palette by index
_TONE_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("睡", "午休", "回笼", "梦"), "#6b7aa1", "#e8edf6"),
    (("课", "学", "自习", "图书馆", "作业", "复习", "考试"), "#3d7ea6", "#e4f1f8"),
    (("写代码", "代码", "编程", "debug", "电脑"), "#5b4fcf", "#ece8ff"),
    (("排练", "社团", "演出", "舞台", "彩排"), "#c45c8a", "#fde8f1"),
    (("出门", "逛街", "散步", "买", "外卖", "快递"), "#d08a2a", "#fff1dc"),
    (("吃饭", "午饭", "晚饭", "早餐", "食堂", "咖啡"), "#c46a3a", "#fdeee4"),
    (("摸鱼", "刷", "视频", "游戏", "看剧"), "#3aa38a", "#e3f6f0"),
    (("下雨", "困在家", "宅", "窝"), "#7a6bb5", "#efeafb"),
]
_PALETTE = [
    ("#6a4bbd", "#efe8fb"),
    ("#2e9e5b", "#e8f6ee"),
    ("#c8881f", "#fbf1dc"),
    ("#e0457b", "#fde8f0"),
    ("#1f9e96", "#e3f5f3"),
    ("#d8423a", "#fde7e5"),
    ("#4a6fd0", "#e7edfb"),
    ("#8a6a3a", "#f4eadc"),
]


def escape_html(s: Any) -> str:
    return html_lib.escape("" if s is None else str(s))


def qq_avatar_url(qq: Any) -> str:
    digits = re.sub(r"\D", "", str(qq or ""))
    if not (5 <= len(digits) <= 12):
        return ""
    return QQ_AVATAR_TEMPLATE.format(qq=digits)


def event_tone(activity: Any, index: int = 0) -> dict[str, str]:
    text = str(activity or "")
    for keys, main, light in _TONE_RULES:
        if any(k in text for k in keys):
            return {"main": main, "light": light}
    main, light = _PALETTE[index % len(_PALETTE)]
    return {"main": main, "light": light}


def encode_avatar_data_uri(image_bytes: bytes, mime: str = "image/png") -> str:
    raw = image_bytes or b""
    if not raw:
        return ""
    if raw.startswith(b"\x89PNG"):
        mime = "image/png"
    elif raw[:2] == b"\xff\xd8":
        mime = "image/jpeg"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        mime = "image/webp"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _shrink_avatar_bytes(image_bytes: bytes, max_side: int = 160) -> bytes:
    """Keep HTML payload small for html2img. Failure → original bytes."""
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(image_bytes))
        im = im.convert("RGBA")
        im.thumbnail((max_side, max_side))
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:
        return image_bytes


def _event_rows(events: list, current_window: str) -> str:
    rows = []
    n = len(events or [])
    for i, ev in enumerate(events or []):
        window = str(ev.get("window") or "")
        activity = escape_html(ev.get("activity") or "")
        mood = escape_html(ev.get("mood") or "")
        tone = event_tone(ev.get("activity") or "", i)
        is_current = bool(current_window) and window == current_window
        cls = "event current" if is_current else "event"
        marker = " data-current='1'" if is_current else ""
        last = " last" if i == n - 1 else ""
        mood_html = f"<span class='mood'>{mood}</span>" if mood else ""
        rows.append(
            f"<div class='slot{last}'>"
            f"<div class='rail'><span class='dot'></span></div>"
            f"<div class='{cls}'{marker} style='--tone:{tone['main']};--tone-bg:{tone['light']}'>"
            f"<span class='window'>{escape_html(window)}</span>"
            f"<span class='activity'>{activity}</span>"
            f"{mood_html}"
            f"</div></div>"
        )
    return "".join(rows)


def _life_card_html(day_card: Optional[dict]) -> str:
    if not day_card:
        return ""
    scene = day_card.get("scene") or {}
    title = escape_html(scene.get("title") or "")
    setting = escape_html(scene.get("setting") or "")
    blurbs = []
    for item in day_card.get("events") or []:
        blurb = escape_html((item or {}).get("blurb") or "")
        if blurb:
            blurbs.append(f"<span class='blurb'>{blurb}</span>")
    blurbs_html = "".join(blurbs)
    scene_line = title
    if setting:
        scene_line = f"{title}<span class='setting'> · {setting}</span>" if title else setting
    return (
        "<div class='life-card'>"
        f"<div class='life-title'>{scene_line}</div>"
        f"<div class='life-blurbs'>{blurbs_html}</div>"
        "</div>"
    )


def build_schedule_html(
    *,
    date_key: str,
    summary: str,
    events: list,
    day_card: Optional[dict] = None,
    current_window: str = "",
    avatar_data_uri: str = "",
    avatar_url: str = "",
    bot_name: str = "",
) -> str:
    date_s = escape_html(date_key)
    summary_s = escape_html(summary)
    name_s = escape_html(bot_name or "今日状态")
    life = _life_card_html(day_card)
    rows = _event_rows(events, current_window)
    w = DEFAULT_VIEWPORT_WIDTH
    avatar_html = ""
    official = (avatar_url or "").strip()
    uri = (avatar_data_uri or "").strip()
    if official and _QLOGO_RE.match(official):
        avatar_html = f"<img class='avatar' src='{escape_html(official)}' alt=''>"
    elif uri.startswith("data:image/") and ";base64," in uri and not re.search(r"https?://", uri, re.I):
        avatar_html = f"<img class='avatar' src='{escape_html(uri)}' alt=''>"
    else:
        initial = (bot_name or "伴")[:1]
        avatar_html = f"<div class='avatar fallback'>{escape_html(initial)}</div>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><style>
*{{box-sizing:border-box}}
body{{margin:0;background:#0f1020;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans CJK SC','Microsoft YaHei',sans-serif;color:#2c2440}}
.page{{width:{w}px;padding:22px;background:linear-gradient(160deg,#3b2a63,#1f1740 60%,#120e2c)}}
.card{{border-radius:28px;background:#fbf8ff;box-shadow:0 22px 60px rgba(10,5,40,.45);overflow:hidden}}
.header{{padding:22px 26px 18px;background:linear-gradient(135deg,#6a4bbd,#8a5fd6);color:#fff}}
.profile{{display:flex;align-items:center;gap:14px}}
.avatar{{width:68px;height:68px;border-radius:999px;object-fit:cover;border:3px solid rgba(255,255,255,.92);box-shadow:0 6px 16px rgba(0,0,0,.28);flex-shrink:0}}
.avatar.fallback{{display:flex;align-items:center;justify-content:center;font-size:28px;font-weight:800;background:rgba(255,255,255,.18)}}
.who{{flex:1;min-width:0}}
.title{{font-size:28px;font-weight:800;letter-spacing:.04em}}
.subtitle{{margin-top:4px;font-size:16px;opacity:.92}}
.name{{margin-top:6px;font-size:15px;opacity:.88}}
.body{{padding:20px 24px 8px}}
.summary{{padding:12px 14px;border-radius:14px;background:#f1ecfb;color:#5b4f86;font-size:15px;line-height:1.55}}
.life-card{{margin-top:14px;padding:12px 14px;border-radius:16px;background:linear-gradient(135deg,#fff5e9,#ffeede);border:1px solid #f3dcc2}}
.life-title{{font-size:16px;font-weight:800;color:#b9722a}}
.setting{{font-weight:500;font-size:13px;color:#8a6a48}}
.life-blurbs{{margin-top:8px;display:flex;flex-wrap:wrap;gap:8px}}
.blurb{{font-size:12px;padding:2px 10px;border-radius:999px;background:#fff;color:#7a5a38;font-weight:600}}
.timeline{{display:flex;flex-direction:column;margin-top:16px}}
.slot{{display:flex;gap:12px;align-items:stretch}}
.rail{{width:18px;position:relative;flex-shrink:0}}
.rail:before{{content:'';position:absolute;left:8px;top:22px;bottom:-6px;width:2px;background:linear-gradient(#d9d0ee,#ece6f5)}}
.slot.last .rail:before{{display:none}}
.dot{{position:absolute;left:3px;top:18px;width:12px;height:12px;border-radius:999px;background:var(--tone,#6a4bbd);border:2px solid #fff;box-shadow:0 0 0 2px color-mix(in srgb, var(--tone,#6a4bbd) 35%, white)}}
.event{{flex:1;margin-bottom:10px;background:var(--tone-bg,#fff);border:1px solid color-mix(in srgb, var(--tone,#6a4bbd) 28%, #ece6f5);border-left:5px solid var(--tone,#6a4bbd);border-radius:16px;padding:12px 14px;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center}}
.event.current{{box-shadow:0 0 0 2px color-mix(in srgb, var(--tone,#6a4bbd) 35%, white);filter:saturate(1.1)}}
.window{{font-size:13px;font-weight:800;color:var(--tone,#6a4bbd);min-width:108px}}
.activity{{flex:1;font-size:16px;font-weight:700;color:#2c2440}}
.mood{{font-size:12px;color:#8a82a6;padding:2px 8px;border-radius:999px;background:rgba(255,255,255,.7)}}
.footer{{padding:10px 26px 18px;text-align:right;font-size:12px;color:#a99ec6}}
</style></head><body><div class='page'><div class='card'>
<div class='header'>
  <div class='profile'>
    {avatar_html}
    <div class='who'>
      <div class='title'>今日日程</div>
      <div class='subtitle'>{date_s}</div>
      <div class='name'>{name_s}</div>
    </div>
  </div>
</div>
<div class='body'>
  <div class='summary'>{summary_s}</div>
  {life}
  <div class='timeline'>{rows}</div>
</div>
<div class='footer'>私人陪伴 · 今日日程</div>
</div></div></body></html>"""


def _load_font(size: int):
    from PIL import ImageFont

    for path in [
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_fallback_png(
    *,
    date_key: str,
    summary: str,
    events: list,
    day_card: Optional[dict] = None,
    avatar_bytes: bytes = b"",
) -> bytes:
    from PIL import Image, ImageDraw

    lines = ["今日日程", str(date_key or ""), str(summary or "")]
    if day_card:
        scene = day_card.get("scene") or {}
        title = scene.get("title") or ""
        setting = scene.get("setting") or ""
        if title or setting:
            lines.append(f"{title} {setting}".strip())
        for item in day_card.get("events") or []:
            blurb = (item or {}).get("blurb") or ""
            if blurb:
                lines.append(str(blurb))
    for ev in events or []:
        window = ev.get("window") or ""
        activity = ev.get("activity") or ""
        mood = ev.get("mood") or ""
        lines.append(f"{window}  {activity}  {mood}".strip())

    W, pad, lh = 560, 36, 40
    header_extra = 76 if avatar_bytes else 0
    H = pad * 2 + header_extra + lh * max(len(lines), 1)
    img = Image.new("RGB", (W, H), (251, 248, 255))
    d = ImageDraw.Draw(img)
    title_font = _load_font(32)
    font = _load_font(22)
    y = pad
    if avatar_bytes:
        try:
            av = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            av.thumbnail((64, 64))
            img.paste(av, (pad, y), av)
        except Exception:
            pass
        y += 8
    for i, line in enumerate(lines):
        f = title_font if i == 0 else font
        x = pad + (76 if avatar_bytes and i == 0 else 0)
        d.text((x, y), line, font=f, fill=(60, 40, 90) if i == 0 else (70, 60, 90))
        y += lh
        if i == 0 and avatar_bytes:
            y = pad + 64 + 12
    # colored left bars for event lines
    ev_start = 3 + (2 if day_card else 0)
    bar_y = pad + header_extra + lh * (3 if not day_card else 3)
    # keep simple: draw tone ticks next to later lines
    line_y = pad + header_extra
    for i, line in enumerate(lines):
        if i >= 3:
            act = ""
            idx = i - (3 + (2 if day_card and day_card.get("events") else 0))
            if 0 <= idx < len(events or []):
                act = str((events[idx] or {}).get("activity") or "")
            tone = event_tone(act, max(idx, 0))
            yy = pad + header_extra + lh * i
            d.rectangle([pad - 10, yy + 6, pad - 4, yy + 28], fill=tone["main"])
        line_y += lh
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


async def html_to_image(
    html: str,
    *,
    renderer_url: str,
    timeout: float = 60,
    post: Optional[Callable] = None,
) -> bytes:
    url = renderer_url.rstrip("/") + "/screenshot"
    payload = {
        "html": html,
        "options": {"fullPage": True, "type": "png"},
        "viewport": {
            "width": DEFAULT_VIEWPORT_WIDTH,
            "height": 1200,
            "deviceScaleFactor": 2,
        },
        "gotoOptions": {"waitUntil": "networkidle0", "timeout": 60000},
        "waitForTimeout": 2500,
        "bestAttempt": True,
    }

    if post is None:
        import httpx

        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.content
    else:
        resp = await post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.content

    if not data.startswith(_PNG_MAGIC):
        raise RuntimeError("渲染返回无效图片")
    return data


async def render_schedule_card(
    *,
    date_key: str,
    summary: str,
    events: list,
    day_card: Optional[dict] = None,
    current_window: str = "",
    renderer_url: str = "",
    timeout: float = 60,
    post: Optional[Callable] = None,
    avatar_bytes: bytes = b"",
    avatar_url: str = "",
    bot_name: str = "",
) -> bytes:
    avatar_uri = ""
    if avatar_bytes:
        avatar_uri = encode_avatar_data_uri(_shrink_avatar_bytes(avatar_bytes))
    official = (avatar_url or "").strip()
    if official and not _QLOGO_RE.match(official):
        official = ""
    if renderer_url:
        try:
            html = build_schedule_html(
                date_key=date_key,
                summary=summary,
                events=events,
                day_card=day_card,
                current_window=current_window,
                avatar_data_uri=avatar_uri,
                avatar_url=official,
                bot_name=bot_name,
            )
            return await html_to_image(
                html,
                renderer_url=renderer_url,
                timeout=timeout,
                post=post,
            )
        except Exception:
            pass
    return render_fallback_png(
        date_key=date_key,
        summary=summary,
        events=events,
        day_card=day_card,
        avatar_bytes=avatar_bytes or b"",
    )
