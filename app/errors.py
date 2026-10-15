"""统一错误体系（设计规格 6.3）。

三件事在这里收口，其余层只管抛 `AppError`：

1. **错误码表**。每层只抛本层错误码：鉴权类 `AUTH_`、业务冲突 `BIZ_`、
   分析与模型 `ANA_`、外部依赖 `EXT_`、系统级 `SYS_`。前端的错误提示与
   401 跳登录都按码段分支（`front/src/api/client.ts`）。
2. **错误码 → HTTP 状态**。放在一处映射，避免同一错误码在不同接口返回不同状态。
3. **统一响应体**。`{code, message, detail, trace_id}`，与前端 `ApiErrorBody` 对齐。

**不把异常原文返回给前端**：未预期异常只回一句通用文案，细节写日志（带 trace_id）。
异常文本里可能带连接串、外部服务的 Authorization 头——那是 8.1 明令要脱敏的东西。
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability import current_trace_id
from engines.common.logging import logger, mask_secret

__all__ = [
    "AppError",
    "CODE_MESSAGES",
    "register_error_handlers",
    "not_authenticated",
    "session_expired",
    "forbidden",
    "bad_credentials",
    "account_disabled",
    "invalid_request",
    "not_found",
    "duplicate",
    "conflict",
]


# 错误码 → (HTTP 状态, 默认文案)。
# AUTH / BIZ / SYS 已在使用；ANA_ 与 EXT_ 是为后续任务预留的段位，
# 列在这里是为了让「新错误该用哪个码」有唯一答案，而不是各写各的。
CODE_MESSAGES: dict[str, tuple[int, str]] = {
    # ---- 认证与授权 ----
    "AUTH_001": (401, "未登录或登录状态无效"),
    "AUTH_002": (401, "登录已过期，请重新登录"),
    "AUTH_003": (403, "当前角色无权执行该操作"),
    "AUTH_004": (401, "用户名或密码错误"),
    "AUTH_005": (403, "账号已停用，请联系管理员"),

    # ---- 业务规则冲突 ----
    "BIZ_201": (404, "资源不存在"),
    "BIZ_202": (409, "资源已存在"),
    "BIZ_203": (409, "操作被业务规则拒绝"),
    "BIZ_400": (422, "请求参数不合法"),
    "BIZ_409": (409, "已有同类任务在执行"),
    "BIZ_410": (409, "上游材料尚未就绪"),

    # ---- 分析与模型（预留）----
    "ANA_301": (502, "模型服务调用失败，请稍后重试"),
    "ANA_302": (422, "样本不足，无法完成聚类"),
    "ANA_303": (422, "内容解析失败"),

    # ---- 外部依赖（预留）----
    "EXT_401": (502, "搜索服务不可用"),
    "EXT_402": (504, "OCR 服务超时"),
    "EXT_403": (502, "通知渠道失败"),

    # ---- 系统级 ----
    "SYS_500": (500, "系统异常，请稍后重试"),
    "SYS_501": (503, "数据库不可用，请稍后重试"),
}


class AppError(Exception):
    """业务异常。**接入层唯一需要认识的异常类型**。

    用法：
        raise AppError("BIZ_201", "主题不存在", detail=f"topic_id={topic_id}")
    """

    def __init__(self, code: str, message: str | None = None, *,
                 detail: str | None = None, http_status: int | None = None) -> None:
        self.code = code
        default_status, default_message = CODE_MESSAGES.get(code, (500, "系统异常"))
        self.message = message or default_message
        self.detail = detail
        self.http_status = http_status or default_status
        super().__init__(f"[{code}] {self.message}")


# ---------------------------------------------------------------------------
# 具名构造器：让调用点读起来是「什么错了」，而不是一串错误码字符串
# ---------------------------------------------------------------------------

def not_authenticated(detail: str | None = None) -> AppError:
    return AppError("AUTH_001", detail=detail)


def session_expired(detail: str | None = None) -> AppError:
    return AppError("AUTH_002", detail=detail)


def forbidden(permission: str | None = None, detail: str | None = None) -> AppError:
    if permission and not detail:
        detail = f"required={permission}"
    return AppError("AUTH_003", detail=detail)


def bad_credentials() -> AppError:
    # 不区分「用户不存在」与「密码错误」：那会变成账号枚举接口
    return AppError("AUTH_004")


def account_disabled() -> AppError:
    return AppError("AUTH_005")


def invalid_request(message: str, detail: str | None = None) -> AppError:
    return AppError("BIZ_400", message, detail=detail)


def not_found(message: str | None = None, detail: str | None = None) -> AppError:
    return AppError("BIZ_201", message, detail=detail)


def duplicate(message: str | None = None, detail: str | None = None) -> AppError:
    """资源已存在（如用户名重复）——与 `conflict` 区分：前端提示不同。"""
    return AppError("BIZ_202", message, detail=detail)


def conflict(message: str | None = None, detail: str | None = None) -> AppError:
    return AppError("BIZ_203", message, detail=detail)


# ---------------------------------------------------------------------------
# 异常处理器
# ---------------------------------------------------------------------------

def _body(code: str, message: str, detail: str | None = None) -> dict:
    return {
        "code": code,
        "message": message,
        "detail": detail,
        "trace_id": current_trace_id(),
    }


# Starlette 内置异常 → 本项目的错误码。内置 404（路径写错）也归到 BIZ_201，
# 保证前端拿到的永远是同一套码段。
# 400 必须映射：**请求体不是合法 JSON** 时 FastAPI 抛的是 HTTPException(400)，
# 不映射就会落到 SYS_500「系统异常」并原样带出英文 detail
# （实测返回过 {"code":"SYS_500","message":"There was an error parsing the body"}）。
_HTTP_STATUS_CODES = {
    400: "BIZ_400",
    401: "AUTH_001",
    403: "AUTH_003",
    404: "BIZ_201",
    405: "BIZ_400",
    409: "BIZ_203",
    422: "BIZ_400",
}

# 500 段的对内置异常的映射（不改变状态码，只给码与文案）
_FALLBACK_CODE = "SYS_500"

# 会话类错误顺手清掉脏 Cookie：否则浏览器会一直带着无效票据，
# 每次请求都先失败一次再跳登录，日志里满是噪音。
_COOKIE_CLEARING_CODES = {"AUTH_001", "AUTH_002"}


def register_error_handlers(app: FastAPI) -> None:
    """注册全部异常处理器（在 `create_app()` 中调用一次）。"""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        payload = _body(exc.code, exc.message, exc.detail)

        if exc.http_status >= 500:
            logger.error("业务异常 {} {} → {}", request.method, request.url.path, exc.code)

        response = JSONResponse(status_code=exc.http_status, content=payload)
        if exc.code in _COOKIE_CLEARING_CODES:
            from app.services.auth.tokens import clear_session_cookie

            clear_session_cookie(response)
        return response

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(request: Request,
                                       exc: RequestValidationError) -> JSONResponse:
        # 只取第一条错误，且不带 loc 的完整结构：前端要的是一句能展示的话
        first = exc.errors()[0] if exc.errors() else {}
        if first.get("type") == "json_invalid":
            # JSON 本身就不合法时，loc 是 body 的下标，拼出来的话没法读
            message = "请求体不是合法的 JSON"
        else:
            field = ".".join(str(p) for p in first.get("loc", ())[1:]) or "请求体"
            message = f"{field}：{first.get('msg', '取值不合法')}"
        return JSONResponse(
            status_code=422,
            content=_body("BIZ_400", message, detail=f"errors={len(exc.errors())}"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request,
                                     exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_STATUS_CODES.get(exc.status_code, _FALLBACK_CODE)
        _, message = CODE_MESSAGES.get(code, (exc.status_code, "请求失败"))
        # 内置异常的 detail 是英文（"Not Found"、"There was an error parsing the body"），
        # 只作为排障信息放进 detail 字段，用户看到的 message 一律用我们的中文文案
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail else None

        response = JSONResponse(status_code=exc.status_code, content=_body(code, message, detail))
        if code in _COOKIE_CLEARING_CODES:
            from app.services.auth.tokens import clear_session_cookie

            clear_session_cookie(response)
        return response

    @app.exception_handler(SQLAlchemyError)
    async def _handle_db_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("数据库异常 {} {}：{}",
                     request.method, request.url.path, mask_secret(str(exc))[:300])
        return JSONResponse(
            status_code=503,
            content=_body("SYS_501", CODE_MESSAGES["SYS_501"][1],
                          detail=type(exc).__name__),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 未预期异常：细节只进日志（带 trace_id），响应里不出现异常原文
        logger.opt(exception=exc).error("未捕获异常 {} {}", request.method, request.url.path)
        return JSONResponse(status_code=500, content=_body("SYS_500", CODE_MESSAGES["SYS_500"][1]))
