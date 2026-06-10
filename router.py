"""WebUI 路由：私人陪伴面板后端接口

挂载在 /plugins/xiaojiu.private_companion/ 下，页面与 API 均为相对路径。
模式参考 nekro_plugin_prompt_injector（mount_router + FileResponse）。
"""

import inspect
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import core
from .plugin import get_config, plugin
from .proactive import pick_motivation, should_send, trigger_proactive
from .state import (
    build_inject_text,
    ensure_daily_state,
    generate_daily_plan,
    generate_diary,
    generate_dream,
    generate_today_state,
)

# ========== 请求模型 ==========


class UserUpdateRequest(BaseModel):
    user_id: str = Field(..., description="陪伴对象 QQ")
    action: str = Field(..., description="enable | disable | reset_quota | reset_ignored")


class RegenerateRequest(BaseModel):
    target: str = Field(..., description="plan | state | dream | diary")


class ProactiveTestRequest(BaseModel):
    user_id: str = Field(..., description="陪伴对象 QQ")


# ========== 工具 ==========


async def _maybe_await(value):
    """兼容契约函数同步/异步两种实现"""
    if inspect.isawaitable(value):
        return await value
    return value


def _norm_should_send(res) -> tuple:
    """归一化 should_send 的返回为 (bool, reason)"""
    if isinstance(res, (tuple, list)) and len(res) >= 2:
        return bool(res[0]), str(res[1])
    if isinstance(res, dict):
        ok = res.get("should_send", res.get("ok", False))
        return bool(ok), str(res.get("reason", ""))
    return bool(res), ""


def _disabled_response() -> Optional[JSONResponse]:
    """插件被禁用时返回 503 风格 JSON，正常时返回 None"""
    enabled = getattr(plugin, "is_enabled", True)
    if callable(enabled):
        enabled = enabled()
    if not enabled:
        return JSONResponse(status_code=503, content={"error": "插件已禁用"})
    return None


# ========== 路由 ==========


@plugin.mount_router()
def create_router() -> APIRouter:
    router = APIRouter()

    # ---------- WebUI 页面 ----------

    @router.get("/", summary="WebUI 面板", include_in_schema=False)
    async def serve_webui():
        html_path = Path(__file__).parent / "webui.html"
        if not html_path.exists():
            return JSONResponse(status_code=404, content={"error": "webui.html 不存在"})
        return FileResponse(str(html_path), media_type="text/html")

    # ---------- 总览 ----------

    @router.get("/api/overview", summary="面板总览")
    async def api_overview():
        resp = _disabled_response()
        if resp:
            return resp
        try:
            cfg = get_config()
            bot_state = await ensure_daily_state()
            plan = bot_state.get("plan") or {}
            st = bot_state.get("state") or {}
            dream = bot_state.get("dream") or {}
            diaries = bot_state.get("diaries") or []
            return {
                "date": bot_state.get("date", ""),
                "plan_summary": plan.get("summary", ""),
                "current_energy": st.get("energy", ""),
                "mood": st.get("mood", ""),
                "conditions": st.get("conditions", []),
                "dream_afterglow": dream.get("afterglow", ""),
                "latest_diary_date": (diaries[-1].get("date", "") if diaries else ""),
                "target_user_count": len(core.target_user_ids()),
                "token_usage_today": await core.get_token_usage_today(),
                "proactive_enabled": cfg.PROACTIVE_ENABLED,
                "inject_enabled": cfg.INJECT_ENABLED,
            }
        except Exception as e:
            return {"error": str(e)}

    @router.get("/api/bot-state", summary="完整生活状态")
    async def api_bot_state():
        resp = _disabled_response()
        if resp:
            return resp
        try:
            return await ensure_daily_state()
        except Exception as e:
            return {"error": str(e)}

    # ---------- 陪伴对象 ----------

    @router.get("/api/users", summary="陪伴对象列表")
    async def api_users():
        resp = _disabled_response()
        if resp:
            return resp
        try:
            cfg = get_config()
            bot_state = await ensure_daily_state()
            users = []
            for uid in core.target_user_ids():
                user_state = await core.get_user_state(uid)
                ok, reason = False, ""
                try:
                    res = await _maybe_await(should_send(uid, user_state, bot_state))
                    ok, reason = _norm_should_send(res)
                except Exception as e:  # noqa: PERF203
                    reason = f"判定失败: {e}"
                item = dict(user_state)
                item["should_send_now"] = ok
                item["reason"] = reason
                item["quota_max"] = cfg.MAX_DAILY_MESSAGES
                users.append(item)
            return users
        except Exception as e:
            return {"error": str(e)}

    @router.post("/api/user/update", summary="修改陪伴对象状态")
    async def api_user_update(req: UserUpdateRequest):
        resp = _disabled_response()
        if resp:
            return resp
        try:
            user_state = await core.get_user_state(req.user_id)
            if req.action == "enable":
                user_state["enabled"] = True
            elif req.action == "disable":
                user_state["enabled"] = False
            elif req.action == "reset_quota":
                user_state["quota_date"] = core.today_key()
                user_state["quota_used"] = 0
            elif req.action == "reset_ignored":
                user_state["ignored_streak"] = 0
            else:
                return {"error": f"未知操作: {req.action}"}
            await core.save_user_state(req.user_id, user_state)
            return user_state
        except Exception as e:
            return {"error": str(e)}

    # ---------- 生成操作 ----------

    @router.post("/api/state/regenerate", summary="重新生成生活内容")
    async def api_regenerate(req: RegenerateRequest):
        resp = _disabled_response()
        if resp:
            return resp
        try:
            if req.target == "plan":
                result = await generate_daily_plan(force=True)
            elif req.target == "state":
                result = await generate_today_state(force=True)
            elif req.target == "dream":
                result = await generate_dream()
            elif req.target == "diary":
                result = await generate_diary(force=True)
            else:
                return {"error": f"未知目标: {req.target}"}
            return {"success": result is not None, "target": req.target, "result": result}
        except Exception as e:
            return {"error": str(e)}

    @router.post("/api/proactive/test", summary="立即触发主动陪伴")
    async def api_proactive_test(req: ProactiveTestRequest):
        resp = _disabled_response()
        if resp:
            return resp
        try:
            user_state = await core.get_user_state(req.user_id)
            bot_state = await ensure_daily_state()
            motivation = await pick_motivation(req.user_id, user_state, bot_state)
            success = await trigger_proactive(req.user_id, motivation, manual=True)
            return {"success": bool(success), "motivation": motivation}
        except Exception as e:
            return {"error": str(e)}

    # ---------- 注入预览 / 日记 ----------

    @router.get("/api/inject-preview", summary="生活状态注入预览")
    async def api_inject_preview():
        resp = _disabled_response()
        if resp:
            return resp
        try:
            return {"text": await build_inject_text()}
        except Exception as e:
            return {"error": str(e)}

    @router.get("/api/diaries", summary="日记列表")
    async def api_diaries():
        resp = _disabled_response()
        if resp:
            return resp
        try:
            bot_state = await ensure_daily_state()
            return bot_state.get("diaries") or []
        except Exception as e:
            return {"error": str(e)}

    return router
