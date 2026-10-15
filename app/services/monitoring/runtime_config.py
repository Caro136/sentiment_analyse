"""运行配置的读取与热更新（FR-53，设计规格 8.1 的「白名单」要求）。

**只接受白名单内的键，且白名单里不含任何密钥。** 这条约束是刻意做实的：
一份能改 `*_API_KEY` 的接口等于把密钥管理下放给了任何一个管理员账号，
而它毫无必要——密钥属于部署配置，改它应当走发布流程。
`tests/test_monitoring.py::ConfigWhitelistTest` 用正则钉死了「白名单不得出现
KEY/SECRET/PASSWORD/TOKEN 一类字样」，将来有人往白名单里加敏感键会立刻失败。

**不改写 `.env`**：热更新只作用于**进程内的配置单例**，重启后回落到 `.env` 的值。
理由有两条——① 让一个 HTTP 接口去重写部署文件，失败模式过多（并发写、权限、格式），
而收益只是「重启后还记得」；② 阈值一类的业务配置本就该落在数据库里
（`alert_rule` 表，T-24），那才是需要持久化的东西。因此本接口面向的是
「运维期临时调参」，GET 会明确标出哪些键被运行时覆盖过（`overridden`）。

**立即生效**：接受更新的对象就是 `get_settings()` 返回的那个单例，它与各模块持有的
是同一个引用，所以下一个请求读到的已是新值。
"""

from dataclasses import dataclass
from typing import Any

from loguru import logger

from app.errors import invalid_request
from app.services.audit import audit, note_change
from engines.contracts.config import Settings, get_settings
from engines.contracts.enums import AuditAction

__all__ = [
    "CONFIG_KEYS",
    "SENSITIVE_KEY_PATTERN",
    "ConfigEntry",
    "read_config",
    "apply_updates",
    "reset_runtime_overrides",
]

# 白名单：键 → (中文名, 说明)。
# 只放「运维期可能想临时调、且不涉及密钥与部署拓扑」的参数。
CONFIG_KEYS: dict[str, tuple[str, str]] = {
    "INTENSITY_W_NEGATIVE": ("烈度权重·负向", "负向内容在烈度指数中的权重（PRD Q1）"),
    "INTENSITY_W_VOLUME": ("烈度权重·声量", "互动量在烈度指数中的权重"),
    "INTENSITY_W_KOL": ("烈度权重·账号", "高权重账号发声的权重"),
    "INTENSITY_W_SENSITIVE": ("烈度权重·敏感", "敏感词命中的权重"),
    "INSIGHT_VECTOR_ENABLED": ("向量召回开关", "关闭后私域召回降级为关键词匹配"),
    "INSIGHT_VECTOR_TOP_K": ("向量召回条数", "语义召回的候选条数上限"),
    "SEARCH_TOOL_TYPE": ("公域搜索供应商", "bocha / tavily / anspire，失败时自动降级"),
    "OCR_TIMEOUT_SECONDS": ("OCR 超时", "单次 OCR 调用超时秒数（超时按解析失败放行）"),
    "ANALYSIS_RETENTION_DAYS": ("分析结果留存天数", "FR-74；原始区不受影响，由重新生成数据集管理"),
    "LOG_LEVEL": ("日志级别", "DEBUG / INFO / WARNING / ERROR，改后即时生效"),
}

# 枚举型键的允许取值。写错供应商名不会报错、只会让公域检索全部降级为空，
# 因此在这里显式校验。
_ALLOWED_VALUES: dict[str, set[str]] = {
    "SEARCH_TOOL_TYPE": {"bocha", "tavily", "anspire"},
    "LOG_LEVEL": {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"},
}

# 敏感键的判定式（供测试断言白名单干净）
SENSITIVE_KEY_PATTERN = r"KEY|SECRET|PASSWORD|TOKEN|AUTHORIZATION|COOKIE"

# 进程内被覆盖过的键。不落盘（见模块说明），仅用于 GET 时标注来源
_overridden: set[str] = set()


@dataclass(frozen=True)
class ConfigEntry:
    key: str
    value: Any
    value_type: str
    label: str
    description: str
    overridden: bool


def read_config(settings: Settings | None = None) -> list[ConfigEntry]:
    """读取白名单内配置的当前值。"""
    cfg = settings or get_settings()
    entries: list[ConfigEntry] = []
    for key, (label, description) in CONFIG_KEYS.items():
        value = getattr(cfg, key.lower(), None)
        entries.append(ConfigEntry(
            key=key,
            value=value,
            value_type=type(value).__name__,
            label=label,
            description=description,
            overridden=key in _overridden,
        ))
    return entries


@audit(AuditAction.CONFIG_UPDATE, target="config")
async def apply_updates(updates: dict[str, Any],
                        settings: Settings | None = None) -> list[ConfigEntry]:
    """应用热更新：校验 → 施加 → 审计（含前后值）。

    键不在白名单或取值非法时**整批拒绝**（而非「忽略非法项、应用其余」）：
    部分成功会让管理员以为改动都生效了，实际只生效一半——这种不报错的半成品状态
    最难排查，宁可整批失败让他重来。

    `@audit` 要求协程函数，因此本函数是 async（内部没有真正的 IO，
    但审计写入是异步的，语义上也确实在一个请求里）。
    """
    if not updates:
        raise invalid_request("没有需要更新的配置项")

    unknown = sorted(set(updates) - set(CONFIG_KEYS))
    if unknown:
        raise invalid_request(
            f"配置项不在可热更新白名单内：{', '.join(unknown)}",
            detail="allowed=" + ", ".join(CONFIG_KEYS))

    cfg = settings or get_settings()
    before = {key: getattr(cfg, key.lower(), None) for key in updates}

    # ⚠️ 必须把环境变量式的大写键转成字段名再交给 pydantic：
    # `Settings(**{"INTENSITY_W_VOLUME": 0.45})` 里的大写键**不是字段名**，
    # 而 model_config 是 extra="ignore" —— 于是它被静默丢弃，构造出一个「值没变」
    # 的新实例，热更新看起来成功、实际什么都没改。这是本项目反复出现的同一类坑
    # （不报错、只是结果不对），测试里有一条专门盯它。
    lowered = {key.lower(): value for key, value in updates.items()}

    # 借 pydantic 做类型转换与校验：字符串 "true"/"0.4" 能正确落到 bool/float，
    # 非法值（如 "abc" 给 int 字段）在这里就报错，而不是等到使用时才炸
    try:
        validated = Settings(**{**cfg.model_dump(), **lowered})
    except Exception as exc:  # noqa: BLE001 —— pydantic 的 ValidationError
        raise invalid_request(f"配置取值不合法：{_first_error(exc)}",
                              detail=f"updates={list(updates)}") from None

    for key in updates:
        value = getattr(validated, key.lower())
        _check_allowed(key, value)
        setattr(cfg, key.lower(), value)
        _overridden.add(key)

    after = {key: getattr(cfg, key.lower(), None) for key in updates}
    note_change(before=before, after=after)

    if any(key == "LOG_LEVEL" for key in updates):
        _reinit_logging(cfg.log_level)

    logger.info("运行配置热更新 {}", after)
    return read_config(cfg)


def _check_allowed(key: str, value: Any) -> None:
    allowed = _ALLOWED_VALUES.get(key)
    if allowed and str(value).upper() not in allowed:
        raise invalid_request(
            f"{key} 取值不合法：{value}",
            detail="allowed=" + "/".join(sorted(allowed)))


def _first_error(exc: Exception) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        items = errors()
        if items:
            location = ".".join(str(p) for p in items[0].get("loc", ()))
            return f"{location} {items[0].get('msg', '取值不合法')}".strip()
    return type(exc).__name__


def _reinit_logging(level: str) -> None:
    """日志级别变了要重建 handler（loguru 的级别是绑在 sink 上的）。"""
    from engines.common.logging import setup_logging

    setup_logging(level)


def reset_runtime_overrides() -> None:
    """清空覆盖标记（测试用；生产要回退请重启服务）。"""
    _overridden.clear()
