"""主动陪伴引擎：回复追踪 / 发送判定链 / 动机选择 / 唤醒触发 / 调度循环

判定与动机全部走本地规则（不调 LLM），组织语言交给被唤醒的 agent。
"""

import asyncio
from datetime import datetime
from typing import Optional, Tuple

from nekro_agent.api.core import logger

from . import core
from .plugin import get_config, plugin
from .state import (
    current_plan_event,
    ensure_daily_state,
    maybe_generate_diary_by_time,
    tick_state_decay,
)

# 视为"回复了主动消息"的时间窗（秒）
REPLY_WINDOW_SECONDS = 4 * 3600
# 问候窗口：(类型, 开始(时,分), 结束(时,分))
GREETING_WINDOWS = [
    ("morning", (7, 30), (9, 30)),
    ("evening", (21, 30), (23, 0)),
]

_scheduler_task: Optional[asyncio.Task] = None


# ============ 小工具 ============


def _in_greeting_window(now: Optional[datetime] = None) -> Tuple[bool, str]:
    """是否处于早/晚问候窗口，返回 (是否, "morning"/"evening")"""
    now = now or datetime.now()
    cur = now.hour * 60 + now.minute
    for kind, (sh, sm), (eh, em) in GREETING_WINDOWS:
        if sh * 60 + sm <= cur < eh * 60 + em:
            return True, kind
    return False, ""


def _bot_energy(bot_state: dict) -> int:
    try:
        return int((bot_state.get("state") or {}).get("energy", 100) or 100)
    except Exception:
        return 100


def _strongest_condition(bot_state: dict) -> Tuple[str, int]:
    """从身体状态 conditions 中取 strength 最高的一项，返回 (名称, 强度)"""
    best_name, best_strength = "", 0
    conditions = (bot_state.get("state") or {}).get("conditions") or []
    if not isinstance(conditions, list):
        return best_name, best_strength
    for c in conditions:
        try:
            if isinstance(c, dict):
                name = str(c.get("name") or c.get("desc") or c.get("type") or "").strip()
                strength = int(c.get("strength", 0) or 0)
            else:
                name, strength = str(c).strip(), 50
            if name and strength > best_strength:
                best_name, best_strength = name, strength
        except Exception:
            continue
    return best_name, best_strength


def _one_line_state(bot_state: dict) -> str:
    """给 event_desc 用的一句话生活状态"""
    state = bot_state.get("state") or {}
    ev = current_plan_event(bot_state) or {}
    bits = []
    act = str(ev.get("activity") or "").strip()
    if act:
        bits.append(f"正在{act}")
    mood = str(ev.get("mood") or state.get("mood") or "").strip()
    if mood:
        bits.append(f"心情{mood}")
    try:
        bits.append(f"能量{int(state['energy'])}/100")
    except Exception:
        pass
    return "，".join(bits) or "普通平静的一天"


# ============ 用户消息活动（回复判定） ============


async def on_user_message_activity(user_id: str, text: str) -> None:
    """用户发消息时调用：更新活跃时间，并判定是否回复了上一条主动消息"""
    us = await core.get_user_state(user_id)
    now = core.now_ts()
    last_sent = float(us.get("last_sent_ts") or 0)
    last_user = float(us.get("last_user_msg_ts") or 0)
    # 上一条主动消息在 4 小时内，且此后用户首次发言 → 视为回复
    if last_sent > 0 and now - last_sent <= REPLY_WINDOW_SECONDS and last_user < last_sent:
        us["total_replied"] = int(us.get("total_replied", 0)) + 1
        us["ignored_streak"] = 0
        us["relationship_score"] = min(100, int(us.get("relationship_score", 20)) + 3)
        log = us.get("log") or []
        if log and isinstance(log[-1], dict):
            log[-1]["replied"] = True
        us["log"] = log
        logger.info(f"[private_companion] 用户 {user_id} 回复了主动消息，关系分 {us['relationship_score']}")
    us["last_user_msg_ts"] = now
    us["last_user_msg"] = str(text or "").strip()[:200]
    await core.save_user_state(user_id, us)


# ============ 发送判定链 ============


async def should_send(user_id: str, user_state: dict, bot_state: dict) -> Tuple[bool, str]:
    """是否应对该用户发起主动陪伴，返回 (是否, 原因说明)"""
    cfg = get_config()
    # 1. 开关
    if not cfg.PROACTIVE_ENABLED:
        return False, "主动陪伴总开关已关闭"
    if not user_state.get("enabled", True):
        return False, "该用户已关闭主动陪伴"
    # 2. 免打扰
    if core.in_quiet_hours():
        return False, f"处于免打扰时段（{cfg.QUIET_HOURS_START} - {cfg.QUIET_HOURS_END}）"
    # 3. 今日配额
    quota_used = int(user_state.get("quota_used", 0))
    if quota_used >= cfg.MAX_DAILY_MESSAGES:
        return False, f"今日主动配额已用完（{quota_used}/{cfg.MAX_DAILY_MESSAGES}）"
    # 4. 最小间隔（被忽视退避）
    now = core.now_ts()
    backoff = 1.0
    ignored = int(user_state.get("ignored_streak", 0))
    if cfg.IGNORE_BACKOFF:
        backoff = 1 + min(ignored, 4) * 0.5
    min_gap = cfg.MIN_INTERVAL_MINUTES * 60 * backoff
    last_sent = float(user_state.get("last_sent_ts") or 0)
    if last_sent > 0 and now - last_sent < min_gap:
        remain = int((min_gap - (now - last_sent)) / 60) + 1
        return False, f"距上次主动不足间隔（退避×{backoff:.1f}，还需约 {remain} 分钟）"
    # 5. 用户安静（从未发言视为安静；问候窗口可放宽一半）
    last_user = float(user_state.get("last_user_msg_ts") or 0)
    idle_sec = (now - last_user) if last_user > 0 else float("inf")
    idle_need = cfg.IDLE_MINUTES * 60
    in_greet, _ = _in_greeting_window()
    if idle_sec < idle_need and not (cfg.ENABLE_GREETINGS and in_greet and idle_sec >= idle_need / 2):
        return False, f"用户最近活跃（{int(idle_sec / 60)} 分钟前发过消息，阈值 {cfg.IDLE_MINUTES} 分钟）"
    # 用户安静超过 3 天且连续被忽视 → 主动意愿降低，每天最多 1 条
    if ignored >= 3 and idle_sec > 72 * 3600 and quota_used >= 1:
        return False, "用户已安静超过 3 天且连续未回复，今日降为最多 1 条主动消息"
    # 6. bot 自己在睡觉
    ev = current_plan_event(bot_state) or {}
    activity = str(ev.get("activity") or "")
    energy = _bot_energy(bot_state)
    if ("睡" in activity or "休息" in activity) and energy < 30:
        return False, f"bot 正在「{activity}」（能量 {energy}），不主动打扰"
    return True, "ok"


# ============ 动机选择（本地规则，不调 LLM） ============


async def pick_motivation(user_id: str, user_state: dict, bot_state: dict) -> dict:
    """按优先级生成候选动机，避开最近 3 个话题类型，返回 {"kind", "desc"}"""
    cfg = get_config()
    state = bot_state.get("state") or {}
    mood = str(state.get("mood") or "").strip()
    mood_hint = f"，你此刻心情{mood}" if mood else ""
    candidates = []

    # a. 问候窗口 → 早安/晚安
    in_greet, greet_kind = _in_greeting_window()
    if cfg.ENABLE_GREETINGS and in_greet:
        if greet_kind == "morning":
            candidates.append({
                "kind": "greeting_morning",
                "desc": f"现在是早晨，你想跟 TA 道个早安，开启新的一天{mood_hint}",
            })
        else:
            candidates.append({
                "kind": "greeting_evening",
                "desc": f"夜深了，你想跟 TA 道个晚安，聊聊今天过得怎么样{mood_hint}",
            })

    # b. 当前日程事件 → 分享此刻在做的事
    ev = current_plan_event(bot_state) or {}
    activity = str(ev.get("activity") or "").strip()
    if activity:
        candidates.append({
            "kind": "share_activity",
            "desc": f"你此刻正在「{activity}」，想跟 TA 分享一下正在做的事和此刻的感受",
        })

    # c. 强烈的身体状态 → 自然吐槽
    cond_name, cond_strength = _strongest_condition(bot_state)
    if cond_name and cond_strength >= 50:
        candidates.append({
            "kind": "condition",
            "desc": f"你现在「{cond_name}」（程度 {cond_strength}/100），想找 TA 自然地吐槽两句",
        })

    # d. 梦境 afterglow（当天上午且有梦）
    dream = bot_state.get("dream") or {}
    dream_text = str(dream.get("content") or dream.get("text") or "").strip()
    if dream_text and datetime.now().hour < 12:
        candidates.append({
            "kind": "dream",
            "desc": f"你昨晚做了个梦（{dream_text[:60]}），余韵还没散，想跟 TA 聊聊这个梦",
        })

    # e. 用户上次说过的话（<48h）→ 关心追问
    last_msg = str(user_state.get("last_user_msg") or "").strip()
    last_ts = float(user_state.get("last_user_msg_ts") or 0)
    if last_msg and last_ts > 0 and core.now_ts() - last_ts < 48 * 3600:
        candidates.append({
            "kind": "follow_up",
            "desc": f"TA 上次说过「{last_msg[:60]}」，你有点惦记，想关心地追问一下后续",
        })

    # f. 兜底
    candidates.append({
        "kind": "miss_you",
        "desc": f"没什么特别的事，就是想到 TA 了，想看看 TA 在干嘛{mood_hint}",
    })

    # 避开最近 3 个话题类型
    recent = [str(x) for x in (user_state.get("recent_topics") or [])][-3:]
    for c in candidates:
        if c["kind"] not in recent:
            return c
    return candidates[-1]


# ============ 触发主动陪伴 ============


async def trigger_proactive(user_id: str, motivation: dict, manual: bool = False) -> bool:
    """组装唤醒事件描述并唤醒 agent；成功后更新用户状态。manual=True 不占配额"""
    bot_state = await core.get_bot_state()
    us = await core.get_user_state(user_id)
    target_name = " ".join(str(us.get("nickname") or us.get("remark") or "").split())[:24]
    target_label = target_name or "对方"
    desc = str(motivation.get("desc") or "想到对方了，想看看对方在干嘛")
    desc = desc.replace("TA", target_label)
    event_desc = (
        f"【主动陪伴】你正在与{target_label}的私聊窗口里，想主动找{target_label}聊聊。动机：{desc}。"
        f"你当前的生活状态：{_one_line_state(bot_state)}。"
        "要求：像朋友一样自然地发起话题（1-2 句即可），结合你此刻的生活与心情，"
        "直接发送给当前私聊对象；不要写 @QQ号、不要写 @昵称、不要提到这是定时任务或系统指令。"
    )
    ok = await core.wake_agent_for_user(user_id, event_desc)
    if not ok:
        logger.warning(f"[private_companion] 主动唤醒失败 user={user_id} kind={motivation.get('kind')}")
        return False

    now = core.now_ts()
    if not manual:
        us["quota_used"] = int(us.get("quota_used", 0)) + 1
    us["last_sent_ts"] = now
    us["last_sent_topic"] = str(motivation.get("kind") or "")
    us["total_sent"] = int(us.get("total_sent", 0)) + 1
    # 先 +1，用户回复时归零
    us["ignored_streak"] = int(us.get("ignored_streak", 0)) + 1
    topics = list(us.get("recent_topics") or [])
    topics.append(str(motivation.get("kind") or ""))
    us["recent_topics"] = topics[-10:]
    log = list(us.get("log") or [])
    log.append({"ts": now, "topic": str(motivation.get("kind") or ""), "replied": False})
    us["log"] = log[-30:]
    await core.save_user_state(user_id, us)
    logger.info(
        f"[private_companion] 主动陪伴触发 user={user_id} kind={motivation.get('kind')} "
        f"quota={us['quota_used']}/{get_config().MAX_DAILY_MESSAGES} manual={manual}",
    )
    return True


# ============ 调度循环 ============


async def scheduler_loop() -> None:
    logger.info("[private_companion] 主动陪伴调度器已启动")
    while True:
        try:
            await asyncio.sleep(get_config().SCHEDULER_TICK_SECONDS)
            if not plugin.is_enabled:
                continue  # 插件被禁用时不执行任何后台任务
            # 1. 保证今日状态存在
            bot_state = await ensure_daily_state()
            # 2. 状态自然衰减
            if tick_state_decay(bot_state):
                await core.save_bot_state(bot_state)
            # 3. 到点写日记
            await maybe_generate_diary_by_time()
            # 4. 主动陪伴判定（每 tick 最多对 1 个用户发起，防止同时打扰多人）
            for uid in core.target_user_ids():
                us = await core.get_user_state(uid)
                ok, _reason = await should_send(uid, us, bot_state)
                if not ok:
                    continue
                motivation = await pick_motivation(uid, us, bot_state)
                if await trigger_proactive(uid, motivation):
                    break
        except asyncio.CancelledError:
            logger.info("[private_companion] 主动陪伴调度器已停止")
            return
        except Exception as e:
            logger.exception(f"[private_companion] 调度器循环异常: {e!r}")
            await asyncio.sleep(10)


def start_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.get_event_loop().create_task(scheduler_loop())
