"""日志初始化：控制台输出 + 全量归档 + 按角色分流。

三个要点：

1. **按角色分流**。每个角色写自己的 `logs/{role}.log`，便于单独排查——
   研判链路一次会跑五个角色，混在一个文件里很难看。
2. **接管标准库日志**。uvicorn、SQLAlchemy 走的是标准 logging，
   通过 `InterceptHandler` 转发到 loguru，保证格式统一、不出现两套日志。
3. **密钥脱敏**。`mask_secret()` 供调用方在记录异常信息时使用；
   外部服务的异常文本常把 Authorization 头或 API Key 原样带出来，
   直接落盘会造成密钥泄露（对应设计规格 8.1 的密钥脱敏要求）。
"""

import logging
import re
import sys

from loguru import logger

from engines.contracts.config import PROJECT_ROOT
from engines.contracts.roles import ROLE_INFOS

LOG_DIR = PROJECT_ROOT / "logs"

CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{extra[role]}</cyan> | "
    "<level>{message}</level>"
)

FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[role]} | "
    "{extra[trace_id]} | {name}:{function}:{line} | {message}"
)

# 匹配常见的密钥形态：sk-xxx、Bearer xxx、长随机串赋值
_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]{8,}", re.IGNORECASE),
    re.compile(r"\b(sk-)[A-Za-z0-9\-_]{8,}", re.IGNORECASE),
    re.compile(
        r"((?:api[_-]?key|password|passwd|secret|token|authorization)\s*[=:]\s*)"
        r"[\"']?([^\s\"',;]{4,})[\"']?",
        re.IGNORECASE,
    ),
]


def mask_secret(text: str) -> str:
    """把文本中的密钥替换为掩码，供日志与错误信息使用。

    >>> mask_secret('Authorization: Bearer abcdefghijklmn')
    'Authorization: Bearer ***'
    """
    if not text:
        return text
    masked = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            masked = pattern.sub(lambda m: f"{m.group(1)}***", masked)
        else:
            masked = pattern.sub(lambda m: f"{m.group(1)}***", masked)
    return masked


def mask_url_credentials(url: str) -> str:
    """抹掉 URL 中的用户名口令（数据库连接串常见）。"""
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:***@", url or "")


def _patch_record(record) -> None:
    """为所有日志记录补默认字段，避免格式化时 KeyError。

    `trace_id` 由接入层的中间件通过 `logger.contextualize()` 注入
    （见 `app/observability.py`）；引擎层的日志不需要知道它存在，
    只要在请求上下文中打印，就会自动带上同一个 id。
    """
    record["extra"].setdefault("role", "-")
    record["extra"].setdefault("trace_id", "-")


class InterceptHandler(logging.Handler):
    """把标准库 logging 的记录转发到 loguru。"""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 回溯到真正的调用点，而不是 logging 的内部帧
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage())


def _role_filter(role_key: str):
    """生成只放行指定角色记录的过滤器。"""
    def _filter(record) -> bool:
        return record["extra"].get("role") == role_key
    return _filter


def setup_logging(level: str = "INFO", *, intercept_stdlib: bool = True) -> None:
    """初始化日志。重复调用会先清空已有 handler，便于测试与配置热更新。"""
    logger.remove()
    logger.configure(patcher=_patch_record)

    logger.add(
        sys.stderr,
        level=level.upper(),
        format=CONSOLE_FORMAT,
        colorize=True,
        backtrace=False,
        diagnose=False,     # 关闭变量值回显：异常现场的局部变量可能含密钥
    )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.add(
        LOG_DIR / "app.log",
        level=level.upper(),
        format=FILE_FORMAT,
        rotation="10 MB",
        retention=5,
        encoding="utf-8",
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )

    # 每个角色一个文件，只接收该角色的记录
    for info in ROLE_INFOS.values():
        logger.add(
            LOG_DIR / info.log_file,
            level=level.upper(),
            format=FILE_FORMAT,
            rotation="10 MB",
            retention=5,
            encoding="utf-8",
            enqueue=True,
            filter=_role_filter(info.key),
            backtrace=False,
            diagnose=False,
        )

    if intercept_stdlib:
        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
        # 这几个库的 INFO 级别噪音较大，统一压到 WARNING
        for noisy in ("uvicorn.access", "sqlalchemy.engine", "asyncio", "aiomysql"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def role_logger(role_key: str):
    """取得绑定了角色标识的 logger，其记录会写入对应的角色日志文件。

    用法：
        log = role_logger(ROLE_INSIGHT)
        log.info("召回完成，命中 {} 条", n)
    """
    return logger.bind(role=role_key)
