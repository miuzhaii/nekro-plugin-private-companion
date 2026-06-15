"""命令处理与钩子：/陪伴 命令、生活状态注入、用户消息活动追踪、调度器启动"""

import asyncio
import base64
from pathlib import Path
from typing import Any

from nekro_agent.adapters.onebot_v11.matchers.command import finish_with
from nekro_agent.api.core import logger
from nekro_agent.api.plugin import SandboxMethodType
from nekro_agent.api.schemas import AgentCtx
from nonebot import get_driver, on_command
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from . import core, proactive
from .plugin import get_config, plugin
from .proactive import start_scheduler
from .state import (
    build_inject_text,
    current_plan_event,
    ensure_daily_state,
    generate_daily_plan,
    generate_diary,
    register_after_daily_plan,
)
from .visuals import (
    build_selfie_capability_prompt,
    get_or_generate_daily_schedule_selfies,
    get_or_generate_schedule_selfie,
    load_persona_visual_profile,
    resolve_safe_image_path,
    schedule_selfie_relative_path,
)

PRIVATE_CHAT_PREFIX = "onebot_v11-private_"


async def _auto_generate_daily_selfies(plan: dict) -> None:
    cfg = get_config()
    if not (cfg.VISUALS_ENABLED and cfg.SELFIE_ENABLED):
        return
    try:
        bot_state = await core.get_bot_state()
        base_dir = _visuals_base_dir()
        profile = _apply_config_defaults_to_visual_profile(load_persona_visual_profile(base_dir))
        events = (plan.get("events") or [])[:cfg.SELFIE_DAILY_LIMIT]
        await get_or_generate_daily_schedule_selfies(
            base_dir,
            events,
            bot_state.get("state") or {},
            profile,
            date_key=core.today_key(),
            generator=_generate_image_with_configured_provider,
            retries=cfg.SELFIE_RETRIES,
            retry_delay=cfg.SELFIE_RETRY_DELAY_SECONDS,
            limit=cfg.SELFIE_DAILY_LIMIT,
        )
        logger.info(f"[private_companion] 日程图片预生成完毕，共 {len(events)} 张")
    except Exception as e:
        logger.warning(f"[private_companion] 日程图片预生成失败: {e!r}")


register_after_daily_plan(_auto_generate_daily_selfies)

HELP_TEXT = """🏠 私人陪伴 - 命令帮助
━━━━━━━━━━━━━━━
• /陪伴 状态 - 今日概括/当前时段/能量心情/各用户配额
• /陪伴 日程 - 查看今日日程
• /陪伴 梦境 - 查看今日梦境
• /陪伴 日记 - 查看最近一篇日记
• /陪伴 自拍 - 生成/发送当前日程自拍
• /陪伴 人设图 - 查看当前人设参考图
• /陪伴 主动 开|关 [QQ] - 开关主动陪伴（管 QQ 仅管理员）
• /陪伴 判定 [QQ] - 调试：当前是否会主动及原因
管理员专用：
• /陪伴 重置日程 - 重新生成今日日程
• /陪伴 生成日记 - 立即生成今日日记
• /陪伴 注入预览 - 查看当前注入的生活状态"""


# ============ 启动调度器 ============

try:
    driver = get_driver()

    @driver.on_startup
    async def _start_companion_scheduler():
        start_scheduler()

except ValueError:
    # NoneBot 未初始化（如独立脚本导入插件模块时），跳过启动钩子
    driver = None


@plugin.on_enabled()
async def _on_plugin_enabled():
    logger.info("[private_companion] 插件已启用，命令与主动陪伴调度恢复")
    start_scheduler()


@plugin.on_disabled()
async def _on_plugin_disabled():
    logger.info("[private_companion] 插件已禁用，命令与主动陪伴停止响应")


# ============ 生活状态注入 ============


@plugin.mount_prompt_inject_method(name="companion_life_state", description="注入 bot 生活状态")
async def companion_life_state(_ctx: AgentCtx) -> str:
    """把今日日程/能量/心情/梦境余韵注入到对话提示词；失败不能影响对话"""
    try:
        text = await build_inject_text(getattr(_ctx, "chat_key", "") or "")
        cfg = get_config()
        if cfg.VISUALS_ENABLED and cfg.SELFIE_ENABLED:
            text += build_selfie_capability_prompt()
        return text
    except Exception as e:
        logger.warning(f"[private_companion] 生活状态注入失败: {e!r}")
        return ""


# ============ 用户消息活动追踪 ============

if hasattr(plugin, "mount_on_user_message"):

    @plugin.mount_on_user_message()
    async def _on_user_message(_ctx: AgentCtx, message: Any) -> None:
        """陪伴对象私聊发消息时更新活跃状态与回复判定，不拦截消息"""
        try:
            chat_key = getattr(_ctx, "chat_key", "") or ""
            if not chat_key.startswith(PRIVATE_CHAT_PREFIX):
                return
            qq = chat_key[len(PRIVATE_CHAT_PREFIX):]
            if qq not in core.target_user_ids():
                return
            try:
                text = getattr(message, "content_text", None) or str(message)
            except Exception:
                text = ""
            await proactive.on_user_message_activity(qq, str(text or ""))
        except Exception as e:
            logger.warning(f"[private_companion] 用户消息钩子异常: {e!r}")

else:
    logger.warning("[private_companion] 当前 nekro 版本不支持 mount_on_user_message，回复判定不可用")


# ============ 权限与工具 ============


def _extract_text(message: Message) -> str:
    return "".join(seg.data.get("text", "") for seg in message if seg.type == "text").strip()


def _is_authorized(user_id: str) -> bool:
    """插件管理员或陪伴对象本人"""
    return core.is_super_admin(user_id) or user_id in core.target_user_ids()


def _fmt_conditions(state: dict) -> str:
    items = []
    for c in state.get("conditions") or []:
        if isinstance(c, dict):
            name = str(c.get("name") or c.get("desc") or c.get("type") or "").strip()
            strength = c.get("strength")
            if name:
                items.append(f"{name}({strength})" if strength is not None else name)
        elif str(c).strip():
            items.append(str(c).strip())
    return "、".join(items) or "无"


def _visuals_base_dir():
    from pathlib import Path

    return Path(str(plugin.get_plugin_data_dir()))


def _apply_config_defaults_to_visual_profile(profile):
    cfg = get_config()
    if not profile.character_prompt.strip() and cfg.PERSONA_VISUAL_PROMPT.strip():
        profile.character_prompt = cfg.PERSONA_VISUAL_PROMPT.strip()
    if not profile.negative_prompt.strip() and cfg.PERSONA_NEGATIVE_PROMPT.strip():
        profile.negative_prompt = cfg.PERSONA_NEGATIVE_PROMPT.strip()
    return profile


async def _send_schedule_selfie_to_chat(ctx: AgentCtx, chat_key: str, force: bool = False) -> dict:
    cfg = get_config()
    if not (cfg.VISUALS_ENABLED and cfg.SELFIE_ENABLED):
        return {"success": False, "error": "视觉资产或日程自拍功能未启用"}
    bot_state = await ensure_daily_state()
    ev = current_plan_event(bot_state) or {}
    if not ev:
        return {"success": False, "error": "当前没有可用日程事件，暂时生成不了自拍"}

    base_dir = _visuals_base_dir()
    date_key = bot_state.get("date") or core.today_key()
    cached_path = base_dir / schedule_selfie_relative_path(ev, date_key)
    if force or not cached_path.exists():
        ok, usage = await _check_and_increment_selfie_quota()
        if not ok:
            return {"success": False, "error": f"今日自拍生成额度已用完（{usage.get('count', 0)}/{cfg.SELFIE_DAILY_LIMIT}）"}
    profile = _apply_config_defaults_to_visual_profile(load_persona_visual_profile(base_dir))
    image_path = await get_or_generate_schedule_selfie(
        base_dir,
        ev,
        bot_state.get("state") or {},
        profile,
        date_key=date_key,
        generator=_generate_image_with_configured_provider,
        hhmm=core.hhmm_now(),
        force=force,
        retries=cfg.SELFIE_RETRIES,
        retry_delay=cfg.SELFIE_RETRY_DELAY_SECONDS,
    )
    sandbox_file = await ctx.fs.mixed_forward_file(str(image_path.resolve()))
    await ctx.ms.send_image(chat_key, sandbox_file, ctx=ctx)
    return {
        "success": True,
        "activity": ev.get("activity", "日常休息"),
        "window": ev.get("window", ""),
        "image": image_path.relative_to(base_dir).as_posix(),
        "generated": force or not cached_path.exists(),
    }


@plugin.mount_sandbox_method(
    SandboxMethodType.TOOL,
    name="发送当前日程自拍",
    description="把当前日程自拍发送到指定聊天。用户想看你、问你现在在做什么或要照片/自拍时使用。",
)
async def send_current_schedule_selfie(_ctx: AgentCtx, chat_key: str = "", force: bool = False) -> dict:
    """发送当前日程自拍到聊天。

    Args:
        chat_key: 目标会话 key。通常传当前上下文的 chat_key；留空时使用当前会话。
        force: 是否强制重拍。默认 False，会优先复用当前时段缓存；用户明确说“重拍/刷新”时才设 True。

    Returns:
        dict: success 为 True 表示图片已发送；False 时 error 为失败原因。
    """
    target_chat_key = str(chat_key or getattr(_ctx, "chat_key", "") or "").strip()
    if not target_chat_key:
        return {"success": False, "error": "缺少目标 chat_key，无法发送自拍"}
    try:
        return await _send_schedule_selfie_to_chat(_ctx, target_chat_key, force=bool(force))
    except Exception as e:
        logger.exception(f"[private_companion] agent 工具发送日程自拍失败: {e!r}")
        return {"success": False, "error": str(e)[:160]}


async def _generate_image_with_configured_provider(prompt: str, reference_image: tuple[Path, str] | None = None) -> str:
    cfg = get_config()
    group_name = str(cfg.SELFIE_MODEL_GROUP or "").strip()
    if group_name:
        from nekro_agent.core.config import config as global_config
        from packages.magic_draw.utils import generate_image_via_chat

        if group_name not in global_config.MODEL_GROUPS:
            raise ValueError(f"未找到配置的绘图模型组: {group_name}")
        reference_images = None
        if reference_image is not None:
            ref_path, ref_desc = reference_image
            mime = "image/jpeg" if ref_path.suffix.lower() in {".jpg", ".jpeg"} else "image/webp" if ref_path.suffix.lower() == ".webp" else "image/png"
            ref_b64 = base64.b64encode(ref_path.read_bytes()).decode("utf-8")
            reference_images = [(f"data:{mime};base64,{ref_b64}", ref_desc)]
        return await generate_image_via_chat(
            global_config.MODEL_GROUPS[group_name],
            prompt,
            timeout=300,
            reference_images=reference_images,
            stream_mode=True,
        )

    from packages.z_img_draw.draw import generate_image

    if reference_image is not None:
        prompt = f"{reference_image[1]}\n\n{prompt}"
    return await generate_image(prompt, aspect_ratio="1:1")


async def _send_local_image(bot: Bot, event: MessageEvent, image_path) -> None:
    from pathlib import Path

    path = Path(image_path).resolve()
    await bot.send(event, Message(MessageSegment.image(path.as_uri())))


async def _check_and_increment_selfie_quota() -> tuple[bool, dict]:
    key = f"visuals_selfie_usage_{core.today_key()}"
    usage = await core.get_json(key, {"date": core.today_key(), "count": 0})
    if not isinstance(usage, dict):
        usage = {"date": core.today_key(), "count": 0}
    limit = int(get_config().SELFIE_DAILY_LIMIT)
    if int(usage.get("count", 0)) >= limit:
        return False, usage
    usage["count"] = int(usage.get("count", 0)) + 1
    await core.set_json(key, usage)
    return True, usage


# ============ /陪伴 ============

@on_command("陪伴", aliases={"私人陪伴"}, priority=5, block=True).handle()
async def handle_companion(matcher: Matcher, event: MessageEvent, bot: Bot, arg: Message = CommandArg()):
    if not plugin.is_enabled:
        return  # 插件已禁用，不响应任何命令
    uid = str(event.user_id)
    if not _is_authorized(uid):
        await finish_with(matcher, message="❌ 仅插件管理员或陪伴对象本人可使用此命令")
    cfg = get_config()
    is_admin = core.is_super_admin(uid)
    parts = _extract_text(arg).split()
    action = parts[0] if parts else ""

    # ---- 帮助 ----
    if action in ("", "帮助", "help"):
        await finish_with(matcher, message=HELP_TEXT)

    # ---- 状态 ----
    if action == "状态":
        bot_state = await ensure_daily_state()
        plan = bot_state.get("plan") or {}
        state = bot_state.get("state") or {}
        ev = current_plan_event(bot_state) or {}
        summary = str(plan.get("summary") or plan.get("overview") or plan.get("theme") or "").strip()
        lines = [f"🏠 今日生活状态（{bot_state.get('date', core.today_key())}）"]
        if summary:
            lines.append(f"• 今日概括: {summary}")
        if ev.get("activity"):
            lines.append(f"• 当前时段: {ev.get('window', '')} {ev.get('activity', '')}".strip())
        else:
            lines.append("• 当前时段: 无日程事件")
        lines.append(f"• 能量: {state.get('energy', '?')}/100  心情: {state.get('mood', '?')}")
        lines.append(f"• 身体状态: {_fmt_conditions(state)}")
        usage = await core.get_token_usage_today()
        limit = cfg.DAILY_TOKEN_LIMIT or "不限"
        lines.append(f"• 今日 token: {usage.get('total', 0)}/{limit}（{usage.get('calls', 0)} 次调用）")
        lines.append("• 主动配额:")
        targets = core.target_user_ids()
        if not targets:
            lines.append("　（未配置陪伴对象）")
        for tid in targets:
            us = await core.get_user_state(tid)
            mark = "✅" if us.get("enabled", True) else "⛔"
            lines.append(
                f"　{mark} {tid}: {us.get('quota_used', 0)}/{cfg.MAX_DAILY_MESSAGES}"
                f"  关系{us.get('relationship_score', 0)}  忽视{us.get('ignored_streak', 0)}",
            )
        await finish_with(matcher, message="\n".join(lines))

    # ---- 日程 ----
    elif action == "日程":
        bot_state = await ensure_daily_state()
        events = (bot_state.get("plan") or {}).get("events") or []
        if not events:
            await finish_with(matcher, message="📅 今日还没有日程（可用「/陪伴 重置日程」生成）")
        lines = [f"📅 今日日程（{bot_state.get('date', core.today_key())}）"]
        for e in events:
            if isinstance(e, dict):
                mood = str(e.get("mood") or "").strip()
                lines.append(
                    f"• {e.get('window', '')} {e.get('activity', '')}" + (f"（{mood}）" if mood else ""),
                )
            else:
                lines.append(f"• {e}")
        await finish_with(matcher, message="\n".join(lines))

    # ---- 重置日程 ----
    elif action == "重置日程":
        if not is_admin:
            await finish_with(matcher, message="❌ 仅插件管理员可重置日程")

        async def _regen_plan():
            try:
                await generate_daily_plan(force=True)
                bot_state = await core.get_bot_state()
                n = len((bot_state.get("plan") or {}).get("events") or [])
                await bot.send(event, f"✅ 今日日程已重新生成，共 {n} 个时段")
            except Exception as e:
                logger.exception(f"[private_companion] 重置日程失败: {e!r}")
                await bot.send(event, f"❌ 日程生成失败: {str(e)[:120]}")

        asyncio.create_task(_regen_plan())
        await finish_with(matcher, message="🔄 正在重新生成今日日程，请稍候...")

    # ---- 梦境 ----
    elif action == "梦境":
        bot_state = await ensure_daily_state()
        dream = bot_state.get("dream") or {}
        content = str(dream.get("content") or dream.get("text") or "").strip()
        if not content:
            await finish_with(matcher, message="💤 今天醒来没记住梦")
        afterglow = str(dream.get("afterglow") or dream.get("mood") or "").strip()
        msg = f"💤 今日梦境\n{content}"
        if afterglow:
            msg += f"\n\n余韵: {afterglow}"
        await finish_with(matcher, message=msg)

    # ---- 日记 ----
    elif action == "日记":
        bot_state = await ensure_daily_state()
        diaries = bot_state.get("diaries") or []
        if not diaries:
            await finish_with(matcher, message="📔 还没有写过日记")
        d = diaries[-1]
        if isinstance(d, dict):
            date = str(d.get("date") or "")
            content = str(d.get("content") or d.get("text") or "").strip()
        else:
            date, content = "", str(d)
        await finish_with(matcher, message=f"📔 最近的日记（{date}）\n{content}")

    # ---- 自拍 ----
    elif action == "自拍":
        if not (cfg.VISUALS_ENABLED and cfg.SELFIE_ENABLED):
            await finish_with(matcher, message="❌ 视觉资产或日程自拍功能未启用，请先在插件配置中开启")
        bot_state = await ensure_daily_state()
        ev = current_plan_event(bot_state) or {}
        if not ev:
            await finish_with(matcher, message="📷 当前没有可用日程事件，暂时生成不了自拍")
        await bot.send(event, "📷 我去拍一张当前日程自拍，稍等一下...")
        try:
            ok, usage = await _check_and_increment_selfie_quota()
            if not ok:
                await finish_with(matcher, message=f"❌ 今日自拍生成额度已用完（{usage.get('count', 0)}/{cfg.SELFIE_DAILY_LIMIT}）")
            profile = load_persona_visual_profile(_visuals_base_dir())
            if not profile.character_prompt.strip() and cfg.PERSONA_VISUAL_PROMPT.strip():
                profile.character_prompt = cfg.PERSONA_VISUAL_PROMPT.strip()
            if not profile.negative_prompt.strip() and cfg.PERSONA_NEGATIVE_PROMPT.strip():
                profile.negative_prompt = cfg.PERSONA_NEGATIVE_PROMPT.strip()
            image_path = await get_or_generate_schedule_selfie(
                _visuals_base_dir(),
                ev,
                bot_state.get("state") or {},
                profile,
                date_key=bot_state.get("date") or core.today_key(),
                generator=_generate_image_with_configured_provider,
                hhmm=core.hhmm_now(),
                force=(len(parts) > 1 and parts[1] in {"重拍", "刷新", "force"}),
                retries=cfg.SELFIE_RETRIES,
                retry_delay=cfg.SELFIE_RETRY_DELAY_SECONDS,
            )
            await bot.send(event, f"我刚在做：{ev.get('activity', '日常休息')}，给你看一眼。")
            await _send_local_image(bot, event, image_path)
            await finish_with(matcher, message="✅ 自拍已发送")
        except Exception as e:
            logger.exception(f"[private_companion] 自拍生成/发送失败: {e!r}")
            await finish_with(matcher, message=f"❌ 自拍失败: {str(e)[:120]}")

    # ---- 人设图 ----
    elif action == "人设图":
        if not cfg.VISUALS_ENABLED:
            await finish_with(matcher, message="❌ 视觉资产功能未启用")
        try:
            profile = load_persona_visual_profile(_visuals_base_dir())
            image_path = resolve_safe_image_path(_visuals_base_dir(), profile.reference_image)
            if not image_path.exists():
                await finish_with(matcher, message="📷 还没有上传人设参考图，请先在 WebUI 上传")
            await bot.send(event, "📷 当前人设参考图：")
            await _send_local_image(bot, event, image_path)
            await finish_with(matcher, message="✅ 已发送")
        except Exception as e:
            await finish_with(matcher, message=f"❌ 人设图读取失败: {str(e)[:120]}")

    # ---- 生成日记 ----
    elif action == "生成日记":
        if not is_admin:
            await finish_with(matcher, message="❌ 仅插件管理员可手动生成日记")

        async def _regen_diary():
            try:
                await generate_diary(force=True)
                bot_state = await core.get_bot_state()
                diaries = bot_state.get("diaries") or []
                d = diaries[-1] if diaries else {}
                content = str(d.get("content") or d.get("text") or "").strip() if isinstance(d, dict) else str(d)
                await bot.send(event, f"✅ 今日日记已生成\n{content[:300]}")
            except Exception as e:
                logger.exception(f"[private_companion] 生成日记失败: {e!r}")
                await bot.send(event, f"❌ 日记生成失败: {str(e)[:120]}")

        asyncio.create_task(_regen_diary())
        await finish_with(matcher, message="🔄 正在生成今日日记，请稍候...")

    # ---- 主动 开|关 ----
    elif action == "主动":
        sub = parts[1] if len(parts) > 1 else ""
        if sub not in ("开", "关"):
            await finish_with(matcher, message="用法: /陪伴 主动 开|关 [QQ号]（管理他人仅限管理员）")
        target = uid
        if len(parts) > 2:
            if not is_admin:
                await finish_with(matcher, message="❌ 仅插件管理员可管理他人的主动陪伴开关")
            target = parts[2].strip()
        if target not in core.target_user_ids():
            await finish_with(matcher, message=f"❌ {target} 不在陪伴对象列表中（请先在插件配置中添加）")
        us = await core.get_user_state(target)
        us["enabled"] = sub == "开"
        await core.save_user_state(target, us)
        await finish_with(matcher, message=f"✅ 已为 {target} {'开启' if sub == '开' else '关闭'}主动陪伴")

    # ---- 判定 ----
    elif action == "判定":
        target = uid
        if len(parts) > 1:
            if not is_admin:
                await finish_with(matcher, message="❌ 仅插件管理员可判定他人")
            target = parts[1].strip()
        if target not in core.target_user_ids():
            await finish_with(matcher, message=f"❌ {target} 不在陪伴对象列表中")
        bot_state = await ensure_daily_state()
        us = await core.get_user_state(target)
        ok, reason = await proactive.should_send(target, us, bot_state)
        msg = f"🔍 主动判定 {target}: {'✅ 会发' if ok else '⛔ 不发'}\n原因: {reason}"
        if ok:
            motivation = await proactive.pick_motivation(target, us, bot_state)
            msg += f"\n候选动机[{motivation.get('kind')}]: {motivation.get('desc')}"
        await finish_with(matcher, message=msg)

    # ---- 注入预览 ----
    elif action == "注入预览":
        if not is_admin:
            await finish_with(matcher, message="❌ 仅插件管理员可预览注入内容")
        chat_key = core.private_chat_key(uid)
        text = await build_inject_text(chat_key)
        await finish_with(matcher, message=f"📋 当前生活状态注入内容:\n{text}" if text.strip() else "📋 当前注入内容为空")

    # ---- 未知子命令 ----
    else:
        await finish_with(matcher, message=f"❓ 未知子命令「{action}」，发送 /陪伴 帮助 查看用法")
