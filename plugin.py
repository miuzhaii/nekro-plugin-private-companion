"""私人陪伴插件 - 插件定义与配置

核心理念移植自 astrbot_plugin_private_companion（精简适配版）：
让 bot 拥有连续的"生活感"（每日日程/能量/心情/梦境/日记），
并对陪伴列表中的用户进行有节制的主动陪伴。

与原版的关键差异：主动消息通过 nekro 原生 timer 唤醒 agent 自己组织语言，
人设/记忆/对话历史全部走 nekro 原生体系。
"""

from typing import List, Literal, Optional

from nekro_agent.api.plugin import ConfigBase, NekroPlugin
from nekro_agent.core.core_utils import ExtraField
from pydantic import Field

try:
    from pydantic import model_validator
except Exception:  # pydantic v1 or missing
    model_validator = None  # type: ignore

MODEL_GROUP_REF = ExtraField(ref_model_groups=True, model_type="chat").model_dump()
DRAW_MODEL_GROUP_REF = ExtraField(ref_model_groups=True, model_type="draw").model_dump()

plugin = NekroPlugin(
    name="私人陪伴",
    module_name="private_companion",
    description="让 bot 拥有连续的生活感（日程/状态/梦境/日记）并主动陪伴指定用户，附 WebUI 面板",
    version="0.3.0",
    author="xiaojiu",
    url="/plugins/xiaojiu.private_companion/",
)


@plugin.mount_config()
class CompanionConfig(ConfigBase):
    # ---------- 陪伴对象 ----------
    TARGET_USER_IDS: List[str] = Field(
        default=[],
        title="陪伴对象 QQ 列表",
        description="只对这些用户进行主动陪伴（每人独立配额与关系状态）；留空则只有生活状态注入，无主动消息",
    )

    # ---------- 人设与模型 ----------
    PERSONA_PRESET_ID: str = Field(
        default="",
        title="陪伴人格（人设）",
        description="生成日程/梦境/日记时使用的人设 ID；为空则尝试使用系统默认人设的内容",
        json_schema_extra=ExtraField(ref_presets=True, ref_presets_no_default=True).model_dump(),
    )
    PERSONA_PROMPT: str = Field(
        default="",
        title="自定义人格描述（优先于上方人设）",
        description="生活状态生成的人格基础，建议简短描述性格/作息/兴趣",
        json_schema_extra=ExtraField(is_textarea=True).model_dump(),
    )
    MODEL_GROUP: str = Field(
        default="",
        title="主模型组",
        description="日记/梦境等内容生成使用的模型组，留空用 nekro 主模型组",
        json_schema_extra=MODEL_GROUP_REF,
    )
    STATE_MODEL_GROUP: str = Field(
        default="",
        title="状态模型组（留空同上）",
        description="日程生成、状态生成等高频轻量任务可指定便宜模型",
        json_schema_extra=MODEL_GROUP_REF,
    )
    LLM_TIMEOUT: int = Field(default=120, title="LLM 请求超时（秒）", ge=30, le=600)
    LLM_RETRIES: int = Field(default=2, title="LLM 重试次数", ge=0, le=5)

    # ---------- 生活状态 ----------
    INJECT_ENABLED: bool = Field(
        default=True,
        title="启用生活状态注入",
        description="把今日日程/能量/心情/梦境余韵注入到对话提示词，让 bot 有连续生活感",
    )
    INJECT_SCOPE: Literal["all", "target_private"] = Field(
        default="all",
        title="状态注入范围",
        description="all=所有会话都注入生活状态 / target_private=仅陪伴对象的私聊注入",
    )
    PLAN_STYLE_HINT: str = Field(
        default="",
        title="日程风格提示（可选）",
        description="影响每日日程生成的补充说明，如「大学生作息」「自由职业画师，常熬夜」",
        json_schema_extra=ExtraField(is_textarea=True).model_dump(),
    )
    DAY_CARD_ENABLED: bool = Field(
        default=True,
        title="启用每日人生卡",
        description="每天抽一张场景+两件事件再生成日程，减少上课/写代码复读。",
    )

    # ---------- 日程图 ----------
    RENDERER_URL: str = Field(
        default="http://nekro_html2img:3000",
        title="HTML 渲染服务地址",
        description="容器内 nekro_html2img 的 /screenshot 地址，用于把今日日程渲染成图；失败回退 Pillow",
    )
    RENDER_TIMEOUT: int = Field(default=60, ge=10, le=180, title="日程图渲染超时（秒）")
    SCHEDULE_CARD_ENABLED: bool = Field(
        default=True,
        title="启用日程图卡",
        description="/陪伴 日程生成 时渲染 HTML 日程图；关闭则只生成文本日程",
    )
    DIARY_TIME: str = Field(
        default="23:10",
        title="日记生成时间",
        description="每天该时间自动写当日日记（HH:MM）",
    )
    KEEP_DIARY_DAYS: int = Field(default=14, title="日记保留天数", ge=3, le=60)

    # ---------- 主动陪伴 ----------
    PROACTIVE_ENABLED: bool = Field(default=True, title="启用主动陪伴")
    MAX_DAILY_MESSAGES: int = Field(
        default=3, title="每人每日主动消息上限", ge=1, le=12,
    )
    MIN_INTERVAL_MINUTES: int = Field(
        default=90, title="同一用户两次主动最小间隔（分钟）", ge=15, le=720,
    )
    IDLE_MINUTES: int = Field(
        default=120,
        title="用户安静阈值（分钟）",
        description="用户最近发过消息则视为活跃，安静超过该时长才考虑主动",
        ge=10, le=1440,
    )
    QUIET_HOURS_START: str = Field(default="23:30", title="免打扰开始（HH:MM）")
    QUIET_HOURS_END: str = Field(default="07:30", title="免打扰结束（HH:MM）")
    ENABLE_GREETINGS: bool = Field(
        default=True,
        title="启用问候窗口",
        description="早(07:30-09:30)晚(21:30-23:00)问候窗口内主动意愿提高",
    )
    IGNORE_BACKOFF: bool = Field(
        default=True,
        title="被忽视自动退避",
        description="用户连续不回复主动消息时，自动拉长主动间隔（最高 3 倍）",
    )
    SCHEDULER_TICK_SECONDS: int = Field(default=45, title="调度器轮询间隔（秒）", ge=15, le=300)

    # ---------- 视觉资产 / 日程自拍 ----------
    VISUALS_ENABLED: bool = Field(default=True, title="启用视觉资产管理")
    SELFIE_ENABLED: bool = Field(default=False, title="启用日程自拍生成")
    SELFIE_MODEL_GROUP: str = Field(
        default="",
        title="自拍图片模型组（绘图模型组）",
        description="选择 Nekro 已配置的绘图(draw)模型组。启用日程自拍时必填；不依赖 magic_draw / z_img_draw 等第三方插件",
        json_schema_extra=DRAW_MODEL_GROUP_REF,
    )
    SELFIE_RETRIES: int = Field(default=1, title="自拍生成失败重试次数", ge=0, le=5)
    SELFIE_RETRY_DELAY_SECONDS: float = Field(default=2.0, title="自拍重试间隔（秒）", ge=0, le=30)
    SELFIE_DAILY_LIMIT: int = Field(default=3, title="每日自拍生成上限", ge=0, le=100)
    SELFIE_CACHE_DAYS: int = Field(default=14, title="自拍缓存保留天数", ge=1, le=90)
    PERSONA_VISUAL_PROMPT: str = Field(
        default="",
        title="默认人设外貌提示词",
        description="WebUI 未单独保存 persona.json 时使用的默认外貌提示词",
        json_schema_extra=ExtraField(is_textarea=True).model_dump(),
    )
    PERSONA_NEGATIVE_PROMPT: str = Field(
        default="low quality, bad hands, extra fingers, blurry, watermark, text",
        title="默认负面提示词",
        json_schema_extra=ExtraField(is_textarea=True).model_dump(),
    )

    # ---------- 预算与权限 ----------
    DAILY_TOKEN_LIMIT: int = Field(
        default=200000,
        title="每日 token 预算（0=不限）",
        description="插件内部 LLM 调用（日程/状态/梦境/日记）的每日总 token 上限，超限后暂停低优先级生成",
        ge=0,
    )
    MANAGE_SUPER_ADMINS: List[str] = Field(
        default=["4592474"],
        title="插件管理员 QQ 列表",
        description="可使用全部 /陪伴 命令；陪伴对象本人可管理自己的开关",
    )

    if model_validator is not None:

        @model_validator(mode="after")
        def _warn_selfie_model_group(self):
            try:
                from nekro_agent.api.core import logger as _logger
            except Exception:
                _logger = None
            if getattr(self, "SELFIE_ENABLED", False) and not str(getattr(self, "SELFIE_MODEL_GROUP", "") or "").strip():
                if _logger is not None:
                    _logger.warning("启用日程自拍时必须配置绘图模型组 SELFIE_MODEL_GROUP")
            return self


def get_config() -> CompanionConfig:
    return plugin.get_config(CompanionConfig)


config: CompanionConfig = get_config()
store = plugin.store
