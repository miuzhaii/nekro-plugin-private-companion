"""WebUI 路由：私人陪伴面板后端接口

挂载在 /plugins/xiaojiu.private_companion/ 下，页面与 API 均为相对路径。
模式参考 nekro_plugin_prompt_injector（mount_router + FileResponse）。
"""

import inspect
import base64
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
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
    current_plan_event,
)
from .visuals import (
    PersonaVisualProfile,
    get_or_generate_daily_schedule_selfies,
    get_or_generate_schedule_selfie,
    is_safe_relative_image_path,
    list_daily_schedule_selfie_status,
    load_persona_visual_profile,
    resolve_safe_image_path,
    save_persona_image_bytes,
    save_persona_visual_profile,
)
# ========== 请求模型 ==========


class UserUpdateRequest(BaseModel):
    user_id: str = Field(..., description="陪伴对象 QQ")
    action: str = Field(..., description="enable | disable | reset_quota | reset_ignored | save_profile")
    remark: str = Field("", description="后台备注名")
    nickname: str = Field("", description="聊天称呼")


class RegenerateRequest(BaseModel):
    target: str = Field(..., description="plan | state | dream | diary")


class ProactiveTestRequest(BaseModel):
    user_id: str = Field(..., description="陪伴对象 QQ")


class VisualProfileRequest(BaseModel):
    character_prompt: str = Field("", description="角色外貌提示词")
    negative_prompt: str = Field("", description="负面提示词")
    style_prompt: str = Field("", description="画风提示词")
    seed_hint: str = Field("", description="可选 seed/一致性提示")


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


def _visuals_base_dir() -> Path:
    return Path(str(plugin.get_plugin_data_dir()))


def _profile_to_api(profile: PersonaVisualProfile) -> dict:
    data = profile.to_dict()
    data["has_reference_image"] = bool(data.get("reference_image") and (_visuals_base_dir() / data["reference_image"]).exists())
    return data


def _apply_config_defaults(profile: PersonaVisualProfile) -> PersonaVisualProfile:
    cfg = get_config()
    if not profile.character_prompt.strip() and cfg.PERSONA_VISUAL_PROMPT.strip():
        profile.character_prompt = cfg.PERSONA_VISUAL_PROMPT.strip()
    if not profile.negative_prompt.strip() and cfg.PERSONA_NEGATIVE_PROMPT.strip():
        profile.negative_prompt = cfg.PERSONA_NEGATIVE_PROMPT.strip()
    return profile


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


async def _check_and_increment_selfie_quota() -> tuple[bool, dict]:
    cfg = get_config()
    key = f"visuals_selfie_usage_{core.today_key()}"
    usage = await core.get_json(key, {"date": core.today_key(), "count": 0})
    if not isinstance(usage, dict):
        usage = {"date": core.today_key(), "count": 0}
    if int(usage.get("count", 0)) >= int(cfg.SELFIE_DAILY_LIMIT):
        return False, usage
    usage["count"] = int(usage.get("count", 0)) + 1
    await core.set_json(key, usage)
    return True, usage


# ========== 路由 ==========


def _build_auth_dependencies() -> list:
    """复用 nekro 主 WebUI 的 JWT 鉴权（管理员登录态）；鉴权模块不可用时失败关闭"""
    try:
        from fastapi import Depends
        from nekro_agent.services.user.deps import get_current_active_user

        return [Depends(get_current_active_user)]
    except Exception as e:  # pragma: no cover
        from nekro_agent.api.core import logger

        logger.error(f"[private_companion] 鉴权依赖加载失败，API 将全部拒绝访问: {e!r}")

        async def _deny():
            from fastapi import HTTPException

            raise HTTPException(status_code=503, detail="鉴权模块不可用")

        from fastapi import Depends

        return [Depends(_deny)]


@plugin.mount_router()
def create_router() -> APIRouter:
    router = APIRouter()
    # API 子路由：全部要求 nekro 登录态（与主 WebUI 同一 JWT）；HTML 页面本身不拦（数据都在 API 里）
    api_router = APIRouter(dependencies=_build_auth_dependencies())

    # ---------- WebUI 页面 ----------

    @router.get("/", summary="WebUI 面板", include_in_schema=False)
    async def serve_webui():
        html_path = Path(__file__).parent / "webui.html"
        if not html_path.exists():
            return JSONResponse(status_code=404, content={"error": "webui.html 不存在"})
        # 禁止缓存，避免改版后浏览器仍跑旧页面
        return FileResponse(
            str(html_path),
            media_type="text/html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
        )

    # ---------- 总览 ----------

    @api_router.get("/api/overview", summary="面板总览")
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

    @api_router.get("/api/bot-state", summary="完整生活状态")
    async def api_bot_state():
        resp = _disabled_response()
        if resp:
            return resp
        try:
            return await ensure_daily_state()
        except Exception as e:
            return {"error": str(e)}

    # ---------- 陪伴对象 ----------

    @api_router.get("/api/users", summary="陪伴对象列表")
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

    @api_router.post("/api/user/update", summary="修改陪伴对象状态")
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
            elif req.action == "save_profile":
                user_state["remark"] = " ".join(str(req.remark or "").split())[:40]
                user_state["nickname"] = " ".join(str(req.nickname or "").split())[:24]
            else:
                return {"error": f"未知操作: {req.action}"}
            await core.save_user_state(req.user_id, user_state)
            return user_state
        except Exception as e:
            return {"error": str(e)}

    # ---------- 生成操作 ----------

    @api_router.post("/api/state/regenerate", summary="重新生成生活内容")
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

    @api_router.post("/api/proactive/test", summary="立即触发主动陪伴")
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

    @api_router.get("/api/inject-preview", summary="生活状态注入预览")
    async def api_inject_preview():
        resp = _disabled_response()
        if resp:
            return resp
        try:
            return {"text": await build_inject_text()}
        except Exception as e:
            return {"error": str(e)}

    @api_router.get("/api/diaries", summary="日记列表")
    async def api_diaries():
        resp = _disabled_response()
        if resp:
            return resp
        try:
            bot_state = await ensure_daily_state()
            return bot_state.get("diaries") or []
        except Exception as e:
            return {"error": str(e)}

    # ---------- 视觉资产 / 日程自拍 ----------

    @api_router.get("/api/visuals/profile", summary="读取视觉人设")
    async def api_visuals_profile():
        resp = _disabled_response()
        if resp:
            return resp
        cfg = get_config()
        if not cfg.VISUALS_ENABLED:
            return JSONResponse(status_code=503, content={"error": "视觉资产功能未启用"})
        profile = _apply_config_defaults(load_persona_visual_profile(_visuals_base_dir()))
        return _profile_to_api(profile)

    @api_router.post("/api/visuals/profile", summary="保存视觉人设")
    async def api_visuals_save_profile(req: VisualProfileRequest):
        resp = _disabled_response()
        if resp:
            return resp
        cfg = get_config()
        if not cfg.VISUALS_ENABLED:
            return JSONResponse(status_code=503, content={"error": "视觉资产功能未启用"})
        profile = load_persona_visual_profile(_visuals_base_dir())
        profile.character_prompt = " ".join(req.character_prompt.split())[:2000]
        profile.negative_prompt = " ".join((req.negative_prompt or cfg.PERSONA_NEGATIVE_PROMPT).split())[:1000]
        profile.style_prompt = " ".join(req.style_prompt.split())[:1000]
        profile.seed_hint = " ".join(req.seed_hint.split())[:200]
        save_persona_visual_profile(_visuals_base_dir(), profile)
        return _profile_to_api(profile)

    @api_router.post("/api/visuals/persona-image", summary="上传/替换人设参考图")
    async def api_visuals_persona_image(file: UploadFile = File(...)):
        resp = _disabled_response()
        if resp:
            return resp
        cfg = get_config()
        if not cfg.VISUALS_ENABLED:
            return JSONResponse(status_code=503, content={"error": "视觉资产功能未启用"})
        content = await file.read()
        try:
            profile = save_persona_image_bytes(_visuals_base_dir(), content, filename=file.filename or "persona.png")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _profile_to_api(profile)

    @api_router.post("/api/visuals/generate-current", summary="生成/读取当前日程自拍")
    async def api_visuals_generate_current(force: bool = False):
        resp = _disabled_response()
        if resp:
            return resp
        cfg = get_config()
        if not (cfg.VISUALS_ENABLED and cfg.SELFIE_ENABLED):
            return JSONResponse(status_code=503, content={"error": "视觉资产或自拍生成功能未启用"})
        bot_state = await ensure_daily_state()
        event = current_plan_event(bot_state) or {}
        if not event:
            return JSONResponse(status_code=400, content={"error": "当前没有可用日程事件"})
        ok, usage = await _check_and_increment_selfie_quota()
        if force and not ok:
            return JSONResponse(status_code=429, content={"error": "今日自拍生成额度已用完", "usage": usage})
        profile = _apply_config_defaults(load_persona_visual_profile(_visuals_base_dir()))
        try:
            image_path = await get_or_generate_schedule_selfie(
                _visuals_base_dir(),
                event,
                bot_state.get("state") or {},
                profile,
                date_key=bot_state.get("date") or core.today_key(),
                generator=_generate_image_with_configured_provider,
                hhmm=core.hhmm_now(),
                force=force,
                retries=cfg.SELFIE_RETRIES,
                retry_delay=cfg.SELFIE_RETRY_DELAY_SECONDS,
            )
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"自拍生成失败: {str(e)[:160]}"})
        rel = image_path.relative_to(_visuals_base_dir()).as_posix()
        return {"success": True, "image": rel, "image_url": f"api/visuals/image/{rel}", "event": event, "usage": usage}

    @api_router.get("/api/visuals/schedule-selfies", summary="列出今日日程自拍状态")
    async def api_visuals_schedule_selfies():
        resp = _disabled_response()
        if resp:
            return resp
        cfg = get_config()
        if not cfg.VISUALS_ENABLED:
            return JSONResponse(status_code=503, content={"error": "视觉资产功能未启用"})
        bot_state = await ensure_daily_state()
        events = bot_state.get("plan", {}).get("events", [])
        if not isinstance(events, list):
            events = []
        date_key = bot_state.get("date") or core.today_key()
        items = list_daily_schedule_selfie_status(_visuals_base_dir(), events, date_key)
        return {"success": True, "date": date_key, "items": items, "total": len(items), "generated": sum(1 for x in items if x.get("exists"))}

    @api_router.post("/api/visuals/generate-day", summary="生成/读取今日全部日程自拍")
    async def api_visuals_generate_day(force: bool = False):
        resp = _disabled_response()
        if resp:
            return resp
        cfg = get_config()
        if not (cfg.VISUALS_ENABLED and cfg.SELFIE_ENABLED):
            return JSONResponse(status_code=503, content={"error": "视觉资产或自拍生成功能未启用"})
        bot_state = await ensure_daily_state()
        events = bot_state.get("plan", {}).get("events", [])
        if not isinstance(events, list) or not events:
            return JSONResponse(status_code=400, content={"error": "今日没有可用日程事件"})
        date_key = bot_state.get("date") or core.today_key()
        profile = _apply_config_defaults(load_persona_visual_profile(_visuals_base_dir()))
        try:
            items = await get_or_generate_daily_schedule_selfies(
                _visuals_base_dir(),
                events,
                bot_state.get("state") or {},
                profile,
                date_key=date_key,
                generator=_generate_image_with_configured_provider,
                force=force,
                retries=cfg.SELFIE_RETRIES,
                retry_delay=cfg.SELFIE_RETRY_DELAY_SECONDS,
                limit=24,
            )
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"今日日程自拍生成失败: {str(e)[:180]}"})
        return {"success": True, "date": date_key, "items": items, "total": len(items), "generated": sum(1 for x in items if x.get("exists"))}

    @api_router.get("/api/visuals/image/{rel_path:path}", summary="读取视觉图片")
    async def api_visuals_image(rel_path: str):
        resp = _disabled_response()
        if resp:
            return resp
        if not is_safe_relative_image_path(rel_path):
            raise HTTPException(status_code=403, detail="unsafe image path")
        try:
            target = resolve_safe_image_path(_visuals_base_dir(), rel_path)
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e)) from e
        if not target.exists():
            raise HTTPException(status_code=404, detail="image not found")
        return FileResponse(str(target), headers={"Cache-Control": "no-cache"})

    router.include_router(api_router)
    return router