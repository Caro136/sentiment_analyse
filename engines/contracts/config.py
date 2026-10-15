"""全局配置：项目所有可变参数的唯一来源。

设计约定（详见 docs/智能舆情分析平台-设计规格.md 附录 C.1）：

1. **所有配置项都有默认值**，缺失 `.env` 时服务仍能启动——缺少密钥只会让对应能力
   在调用时降级并给出明确提示，而不是启动即崩。这对开发环境尤其重要。
2. **角色级模型配置**通过 `LLMRoleSettings` 表达，环境变量形如
   `INSIGHT_ENGINE_API_KEY`（前缀 = 字段名，后缀 = 子字段名）。
3. **配置全部可热更新**：`reload_settings()` 清缓存后重新读取 `.env`，
   对应 PRD FR-53 与管理端配置接口。

配置项不得散落在业务代码中直接读环境变量——一律经本模块取值。
"""

import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# engines/contracts/config.py → 上溯三层为项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = PROJECT_ROOT / "docs" / "reports"
ENV_FILE = PROJECT_ROOT / ".env"

# 云端可用的默认向量编码模型
DEFAULT_EMBEDDING_MODEL = "text-embedding-v4"

# 盘符开头（C:\...）或含路径分隔符 → 判定为本地模型路径而非云端模型名
_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]|[\\/]")


def _looks_like_local_path(value: str) -> bool:
    """判断配置值是不是本地模型目录而非云端模型名。"""
    return bool(_PATH_PATTERN.search(value or ""))


class LLMRoleSettings(BaseModel):
    """单个角色的模型接入配置。

    各角色独立配置是有意为之：打标（analytics）追求吞吐与成本，
    报告生成追求成稿质量，主持人追求稳定的判断与总结——用同一个模型是浪费。
    """

    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    # 留空则使用模型默认值。打标类角色建议低温度（结果稳定），
    # 撰写类角色可略高（表达多样）。环境变量形如 INSIGHT_ENGINE_TEMPERATURE。
    temperature: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float = 120.0

    @property
    def configured(self) -> bool:
        """是否已具备调用条件（用于启动自检与降级提示）。"""
        return bool(self.api_key and self.model_name)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",             # 允许 .env 中存在未被使用的键
        # ⚠️ 不要设置 env_nested_delimiter：角色字段名本身含下划线
        # （insight_engine），与 "_" 分隔符产生切分歧义——
        # INSIGHT_ENGINE_API_KEY 无法确定是 insight_engine.api_key 还是
        # insight.engine.api.key，结果是**整个 .env 静默解析失败**。
        # 角色配置因此改用扁平字段，见下方「角色级模型配置」。
    )

    # ---------------- 服务 ----------------
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---------------- 数据库 ----------------
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "sentiment_monitor"
    db_charset: str = "utf8mb4"

    # ---------------- 角色级模型配置 ----------------
    # 扁平字段而非嵌套模型。嵌套写法更优雅，但与 pydantic-settings 的环境变量
    # 解析机制冲突（见 model_config 中的说明），会静默读不到任何值。
    # 由 llm_for_role() 组装为 LLMRoleSettings 供调用方使用。

    analytics_api_key: str = ""
    analytics_base_url: str = ""
    analytics_model_name: str = ""
    analytics_temperature: float | None = None
    analytics_max_tokens: int | None = None
    analytics_timeout_seconds: float = 120.0

    insight_engine_api_key: str = ""
    insight_engine_base_url: str = ""
    insight_engine_model_name: str = ""
    insight_engine_temperature: float | None = None
    insight_engine_max_tokens: int | None = None
    insight_engine_timeout_seconds: float = 120.0

    media_engine_api_key: str = ""
    media_engine_base_url: str = ""
    media_engine_model_name: str = ""
    media_engine_temperature: float | None = None
    media_engine_max_tokens: int | None = None
    media_engine_timeout_seconds: float = 120.0

    # 前缀为 host_agent 而非 host，避免与服务监听地址 HOST 冲突
    host_agent_api_key: str = ""
    host_agent_base_url: str = ""
    host_agent_model_name: str = ""
    host_agent_temperature: float | None = None
    host_agent_max_tokens: int | None = None
    host_agent_timeout_seconds: float = 120.0

    report_engine_api_key: str = ""
    report_engine_base_url: str = ""
    report_engine_model_name: str = ""
    report_engine_temperature: float | None = None
    report_engine_max_tokens: int | None = None
    report_engine_timeout_seconds: float = 120.0

    # ---------------- 公域搜索 ----------------
    search_tool_type: str = "bocha"
    tavily_api_key: str = ""
    bocha_base_url: str = "https://api.bocha.cn/v1/ai-search"
    bocha_api_key: str = ""
    anspire_base_url: str = "https://plugin.anspire.cn/api/ntsearch/search"
    anspire_api_key: str = ""

    # ---------------- 向量召回 ----------------
    insight_vector_enabled: bool = True
    milvus_uri: str = "http://localhost:19530"
    milvus_db_name: str = "default"
    milvus_insight_collection: str = "insight_evidence"
    insight_vector_top_k: int = 80

    # ---------------- 文本向量编码 ----------------
    # 留空时回落到 DashScope 配置（同一套密钥通常可共用），见 embedding_settings
    embedding_model: str = ""
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_batch_size: int = 10      # DashScope 的单次请求上限；超限时编码器会自动拆分

    # ---------------- OCR 云服务（MCP / SSE）----------------
    ocr_mcp_url: str = "https://dashscope.aliyuncs.com/api/v1/mcps/ai-ocr/sse"
    dashscope_api_key: str = ""
    ocr_timeout_seconds: float = 20.0

    # ---------------- 邮件告警 ----------------
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""      # QQ 邮箱此处填「授权码」，非登录密码
    alert_mail_to: str = ""

    # ---------------- 舆情烈度指数权重（决定风险分级）----------------
    # 约束到 [0, 1]：这四个值经 /api/v1/config 可热更新，一个 1.5 之类的取值会让
    # 烈度指数整体越界、风险分级失去意义（PRD Q1 的权重只有相对意义）
    intensity_w_negative: float = Field(default=0.50, ge=0.0, le=1.0)
    intensity_w_volume: float = Field(default=0.30, ge=0.0, le=1.0)
    intensity_w_kol: float = Field(default=0.12, ge=0.0, le=1.0)
    intensity_w_sensitive: float = Field(default=0.15, ge=0.0, le=1.0)

    # ---------------- 报告产物目录 ----------------
    # 留空则回落到项目内默认路径（见下方 model_post_init）
    output_dir: str = ""
    host_report_dir: str = ""
    insight_report_dir: str = ""
    media_report_dir: str = ""

    # ---------------- 数据留存（FR-74）----------------
    # 分析结果的保留天数。原始区不受它影响——原始表是数据生成器的交付物，
    # ADR-03 规定本项目只读，「原始数据的留存」由重新生成数据集来管理。
    analysis_retention_days: int = 180

    # ---------------- 会话 ----------------
    session_secret: str = ""
    session_max_age_seconds: int = 8 * 3600
    session_cookie_name: str = "sentiment_session"
    # 生产走 HTTPS 时必须置 true（否则票据在网络中以明文传输）。
    # 默认 false 是为了本地 http 演示：置 true 会让浏览器直接丢弃 Cookie，
    # 表现为「登录成功却立刻又要登录」。
    session_cookie_secure: bool = False

    def model_post_init(self, __context) -> None:  # noqa: D105
        """把留空的目录配置回落到项目内默认路径。

        用留空而非直接写默认值，是为了让 .env 中「未填写」与「填写了空值」
        有一致行为（都走默认），避免出现空字符串路径导致写文件失败。
        """
        defaults = {
            "output_dir": DEFAULT_REPORT_DIR,
            "host_report_dir": DEFAULT_REPORT_DIR / "host",
            "insight_report_dir": DEFAULT_REPORT_DIR / "insight",
            "media_report_dir": DEFAULT_REPORT_DIR / "media",
        }
        for attr, default in defaults.items():
            if not getattr(self, attr):
                setattr(self, attr, str(default))

    # ---------------- 派生属性与方法 ----------------

    @property
    def database_url(self) -> str:
        """SQLAlchemy 异步连接串。口令做 URL 编码，避免特殊字符破坏连接串。"""
        return (
            f"mysql+aiomysql://{quote_plus(self.db_user)}:{quote_plus(self.db_password)}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset={self.db_charset}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def embedding_settings(self) -> tuple[str, str, str]:
        """文本向量编码的 (api_key, base_url, model)。

        未单独配置时回落到 DashScope：向量编码与 OCR 用的是同一家的服务，
        再让使用者重复填一遍密钥没有意义。

        ⚠️ `EMBEDDING_MODEL` 可能被配成本地模型路径（课程资料里的 bge-m3 目录）。
        那种值发给云端接口必然 404，且报错信息（"model F:\\... does not exist"）
        很容易被误读成"没有权限"。识别出路径形态就回落到云端默认模型。
        """
        api_key = self.embedding_api_key or self.dashscope_api_key
        base_url = self.embedding_base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        model = self.embedding_model
        if not model or _looks_like_local_path(model):
            model = DEFAULT_EMBEDDING_MODEL
        return api_key, base_url, model

    @property
    def intensity_weights(self) -> dict[str, float]:
        """烈度指数四项权重（对应设计规格 Q1，可按业务反馈调整后重跑评测集）。"""
        return {
            "negative": self.intensity_w_negative,
            "volume": self.intensity_w_volume,
            "kol": self.intensity_w_kol,
            "sensitive": self.intensity_w_sensitive,
        }

    def report_dir_for_role(self, role_key: str) -> Path:
        """按角色取报告产物目录，目录不存在时创建。"""
        from .roles import get_role_info

        info = get_role_info(role_key)
        if info.report_dir_attr is None:
            raise ValueError(f"角色 {role_key!r} 没有独立的报告目录")
        path = Path(getattr(self, info.report_dir_attr))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def llm_for_role(self, role_key: str) -> LLMRoleSettings:
        """按角色取模型配置。未知角色直接报错，避免静默用错模型。

        由扁平字段组装：角色前缀（analytics / insight_engine / ...）来自角色注册表，
        新增角色时只需在 roles.py 登记并在本类补上对应字段。
        """
        from .roles import get_role_info

        prefix = get_role_info(role_key).config_attr

        def _get(suffix: str, default=None):
            return getattr(self, f"{prefix}_{suffix}", default)

        return LLMRoleSettings(
            api_key=_get("api_key", ""),
            base_url=_get("base_url", ""),
            model_name=_get("model_name", ""),
            temperature=_get("temperature"),
            max_tokens=_get("max_tokens"),
            timeout_seconds=_get("timeout_seconds", 120.0),
        )

    def startup_diagnostics(self) -> list[tuple[str, bool, str]]:
        """启动自检：列出各能力的就绪情况，便于快速定位「没配密钥」。

        返回 (能力名称, 是否就绪, 说明) 列表。未就绪不影响启动，只影响对应功能。
        """
        from .roles import ROLE_INFOS

        checks: list[tuple[str, bool, str]] = []

        for info in ROLE_INFOS.values():
            cfg = self.llm_for_role(info.key)
            checks.append((
                f"模型配置 / {info.display_name}",
                cfg.configured,
                f"{cfg.model_name}" if cfg.configured else "未配置 API_KEY 或 MODEL_NAME",
            ))

        checks.append((
            "搜索服务",
            bool(self.tavily_api_key or self.bocha_api_key or self.anspire_api_key),
            f"当前供应商 {self.search_tool_type}",
        ))
        checks.append((
            "OCR 服务（MCP）",
            bool(self.dashscope_api_key),
            self.ocr_mcp_url if self.dashscope_api_key else "未配置 DASHSCOPE_API_KEY",
        ))
        checks.append((
            "邮件告警",
            bool(self.smtp_user and self.smtp_password and self.alert_mail_to),
            "已配置" if (self.smtp_user and self.smtp_password) else "未配置 SMTP_USER / SMTP_PASSWORD（QQ 邮箱需授权码）",
        ))
        checks.append((
            "向量召回",
            self.insight_vector_enabled,
            f"Milvus {self.milvus_uri}" if self.insight_vector_enabled else "已在配置中关闭",
        ))
        checks.append((
            "会话密钥",
            bool(self.session_secret),
            "已配置" if self.session_secret else "未配置，服务将生成临时密钥（重启后会话失效）",
        ))
        return checks


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例。"""
    return Settings()


def reload_settings() -> Settings:
    """重新读取 .env 并刷新缓存（对应 PRD FR-53 配置热更新）。"""
    get_settings.cache_clear()
    return get_settings()
