"""会话票据：签名（itsdangerous）而非服务端会话表。

**为什么用签名 Cookie**：前端（T-06）已约定同源 Cookie 自动携带
——`EventSource` 无法自定义请求头，SSE 要能鉴权就只能靠 Cookie。
业务区表结构已冻结（ADR-03 的分区约定），没有会话表，
因此票据必须自证身份：内容签名 + 时间戳，服务端不存任何状态。

票据载荷只有两项，**不放角色与显示名**：

    {"uid": 用户业务ID, "pwd": 口令哈希指纹}

角色、状态、显示名一律在请求时回查数据库（一次主键查询）。这样管理员改角色、
停用账号能**立即生效**，而不是等票据过期——把权限信息塞进票据的常见做法在这里
会变成「停用了还在用」的漏洞。

**撤销能力**：无状态票据天然无法撤销，所以载荷里带上口令指纹——
改口令 / 重置口令后，旧票据的指纹对不上，全部会话立刻失效。
这是无会话表时唯一可靠的撤销入口，也覆盖了 FR-03 的「重置密码」语义。
代价是「仅登出当前设备」以外的强制下线需要改口令，已在 README 说明。

密钥来源：`SESSION_SECRET` 未配置时使用进程内随机密钥并告警
——此时重启即全体掉线，开发期可接受（与启动自检的提示一致）。
"""

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from engines.common.logging import logger
from engines.contracts.config import Settings, get_settings

__all__ = [
    "SESSION_SALT",
    "SessionPayload",
    "SessionError",
    "SessionExpired",
    "SessionInvalid",
    "issue_session",
    "read_session",
    "set_session_cookie",
    "clear_session_cookie",
    "ephemeral_secret_active",
]

SESSION_SALT = "sentiment.session.v1"

# 未配置 SESSION_SECRET 时的进程内密钥。用 secrets 而非硬编码常量：
# 硬编码等于所有人共用同一个签名密钥，比"重启掉线"危险得多。
_EPHEMERAL_SECRET = secrets.token_urlsafe(48)
_ephemeral_warned = False


class SessionError(Exception):
    """会话票据不可用的基类。"""


class SessionExpired(SessionError):
    """票据签名有效但已超时（对应错误码 AUTH_002）。"""


class SessionInvalid(SessionError):
    """票据缺失、被篡改或格式不符（对应错误码 AUTH_001）。"""


@dataclass(frozen=True)
class SessionPayload:
    """票据载荷。`fingerprint` 是口令哈希指纹，见模块说明。"""

    user_id: str
    fingerprint: str


def _secret(settings: Settings | None = None) -> str:
    """取签名密钥。留空时回落到进程内随机密钥并告警一次。"""
    global _ephemeral_warned
    cfg = settings or get_settings()
    if cfg.session_secret:
        return cfg.session_secret

    if not _ephemeral_warned:
        _ephemeral_warned = True
        logger.warning(
            "未配置 SESSION_SECRET，会话使用进程内临时密钥——服务重启后所有登录失效。"
            "生产环境请在 .env 中设置 SESSION_SECRET。"
        )
    return _EPHEMERAL_SECRET


def ephemeral_secret_active(settings: Settings | None = None) -> bool:
    """当前是否在使用临时密钥（供启动诊断与测试断言）。"""
    return not (settings or get_settings()).session_secret


def _serializer(settings: Settings | None = None) -> URLSafeTimedSerializer:
    # itsdangerous 默认用 SHA-1 签名，这里显式换成 SHA-256
    return URLSafeTimedSerializer(
        _secret(settings),
        salt=SESSION_SALT,
        signer_kwargs={"digest_method": hashlib.sha256},
    )


def issue_session(user_id: str, password_hash: str, *,
                  settings: Settings | None = None) -> str:
    """签发会话票据。"""
    payload: dict[str, Any] = {
        "uid": user_id,
        "pwd": _fingerprint(password_hash),
    }
    return _serializer(settings).dumps(payload)


def read_session(token: str, *, settings: Settings | None = None,
                 max_age: int | None = None) -> SessionPayload:
    """校验并解析票据。

    失败一律抛 `SessionError` 子类，由接入层翻译成 AUTH_001 / AUTH_002：
    过期与非法分开，是因为前端对二者的处理不同——过期提示"登录已失效"，
    非法（被篡改）需要额外告警。
    """
    if not token:
        raise SessionInvalid("缺少会话票据")

    cfg = settings or get_settings()
    age = cfg.session_max_age_seconds if max_age is None else max_age

    try:
        data = _serializer(cfg).loads(token, max_age=age)
    except SignatureExpired as exc:
        raise SessionExpired("会话已过期，请重新登录") from exc
    except BadSignature as exc:
        raise SessionInvalid("会话票据无效") from exc

    if not isinstance(data, dict) or not data.get("uid"):
        raise SessionInvalid("会话票据格式不符")

    return SessionPayload(user_id=str(data["uid"]), fingerprint=str(data.get("pwd", "")))


def _fingerprint(password_hash: str) -> str:
    """与 passwords.password_fingerprint 同一算法。

    这里内联实现而非导入，是为了让 tokens 模块保持零项目内依赖，
    可以脱离服务层单独测试；两处算法由单测钉死一致。
    """
    return hashlib.sha256((password_hash or "").encode("utf-8")).hexdigest()[:16]


def set_session_cookie(response, token: str, *, settings: Settings | None = None) -> None:
    """把票据写入 Cookie。

    `httponly` 挡住 XSS 读取；`samesite=lax` 让跨站表单提交不带凭据（CSRF 的基本防护），
    同时不影响同源前端与 SSE。`secure` 由配置决定——本地 http 演示下必须为 False，
    否则浏览器直接丢弃该 Cookie，表现为"登录成功但立刻又要登录"。
    """
    cfg = settings or get_settings()
    response.set_cookie(
        key=cfg.session_cookie_name,
        value=token,
        max_age=cfg.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=cfg.session_cookie_secure,
        path="/",
    )


def clear_session_cookie(response, *, settings: Settings | None = None) -> None:
    """清除 Cookie（登出，以及会话失效时顺手清掉脏票据）。"""
    cfg = settings or get_settings()
    response.delete_cookie(
        key=cfg.session_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=cfg.session_cookie_secure,
    )
