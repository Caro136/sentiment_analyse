"""FastAPI 应用装配。

职责边界：只做「创建实例、挂载中间件、注册路由、管理生命周期」。
业务逻辑一律下沉到 `app/services/` 与 `engines/`——接入层保持轻量。
"""

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.errors import register_error_handlers
from app.observability import TraceIdMiddleware
from app.routers import health
from app.routers.alerts import router as alert_router
from app.routers.auth import public_router as auth_public_router
from app.routers.auth import router as auth_router
from app.routers.channels import router as channels_router
from app.routers.config import router as config_router
from app.routers.contents import router as contents_router
from app.routers.dashboard import router as dashboard_router
from app.routers.reports import router as report_router
from app.routers.research import router as research_router
from app.routers.system import router as system_router
from app.routers.events import router as event_router
from app.routers.sse import router as sse_router
from app.routers.tickets import event_router as ticket_event_router
from app.routers.tickets import router as ticket_router
from app.routers.topics import router as topics_router
from app.routers.users import router as users_router
from app.security import require_authenticated
from engines.common.logging import logger, mask_url_credentials, setup_logging
from engines.contracts.config import get_settings

APP_TITLE = "智能舆情分析平台"
APP_VERSION = "0.1.0"


def _log_startup_diagnostics() -> None:
    """启动时打印配置就绪情况。

    未就绪的能力不影响服务启动，但会在日志中明确标出——避免出现
    「服务起来了，但研判一调用就报错，排查半天才发现没配 API Key」。
    """
    settings = get_settings()
    logger.info("=" * 64)
    logger.info("{} v{} 启动中，监听 {}:{}", APP_TITLE, APP_VERSION,
                settings.host, settings.port)
    logger.info("数据库：{}", mask_url_credentials(settings.database_url))
    logger.info("日志级别：{}", settings.log_level)
    logger.info("-" * 64)

    not_ready = []
    for name, ready, detail in settings.startup_diagnostics():
        if ready:
            logger.info("  [就绪] {}", name)
        else:
            logger.warning("  [未配置] {} —— {}", name, detail)
            not_ready.append(name)

    if not_ready:
        logger.warning("-" * 64)
        logger.warning("共 {} 项能力未就绪，相关接口调用时会降级或报错。",
                       len(not_ready))
        logger.warning("请参考 .env.example 补齐配置。")
    logger.info("=" * 64)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log_startup_diagnostics()

    # SSE 广播跟随应用生命周期启停：启动时订阅事件总线，关闭时清理连接。
    # 放在 lifespan 而不是模块导入时，是为了让测试能反复创建/销毁应用。
    from app.services.dashboard import get_refresher
    from app.services.realtime import get_broadcaster
    from engines.alerting import get_listener

    broadcaster = get_broadcaster()
    broadcaster.start()

    # 预警监听器跟随应用生命周期：订阅「内容已打标」事件，驱动规则扫描（T-24）。
    # 与事件总线同理，进程内状态要求单 worker（见 ADR-05）。
    listener = get_listener()
    listener.start()

    # 大盘指标定时物化（T-26，设计规格 8.2：每 60 秒一轮）
    refresher = get_refresher()
    refresher.start()

    # 收尾上一轮进程中断留下的在途任务（T-27）：报告停在 generating 会让前端永远
    # 显示「生成中」，而且判重会永久挡住该事件。失败只记日志——服务该起还是要起。
    try:
        from app.services.report.service import recover_interrupted

        recovered = await recover_interrupted()
        if recovered["interrupted_reports"]:
            logger.warning("已把 {} 份中断的报告标记为失败：{}",
                           len(recovered["interrupted_reports"]),
                           ", ".join(recovered["interrupted_reports"]))
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).warning("清理中断任务失败（不影响服务启动）")

    yield

    refresher.stop()
    listener.stop()
    broadcaster.stop()
    logger.info("{} 已关闭", APP_TITLE)


def _build_routers() -> tuple[APIRouter, APIRouter]:
    """装配两个路由组：公开组与受保护组（设计规格 8.1「默认拒绝」）。

    受保护组挂着 `require_authenticated` 依赖，**加进这个组的路由自动要求登录**。
    新增业务路由时的默认动作就是把 router 加进这里——忘记加的结果是「默认拒绝」
    （接口 404/未注册），而不是「默认可访问」，失败方向是安全的。

    公开组目前只有登录：它是全站唯一不需要会话的接口。
    """
    public = APIRouter()
    public.include_router(auth_public_router)

    protected = APIRouter(dependencies=[Depends(require_authenticated)])
    protected.include_router(auth_router)      # 登出、当前用户
    protected.include_router(users_router)     # 用户管理（T-19）
    protected.include_router(topics_router)    # 监测主题与词包（T-21）
    protected.include_router(channels_router)  # 信源通道（T-21）
    protected.include_router(config_router)    # 运行配置热更新（T-21）
    protected.include_router(contents_router)  # 舆情内容查询（T-22）
    protected.include_router(event_router)     # 事件与溯源（T-23）
    protected.include_router(alert_router)     # 预警（T-24）
    protected.include_router(research_router)  # 研判任务（T-16 的接入部分，T-34 补齐）
    protected.include_router(ticket_router)    # 处置工单（T-25）
    protected.include_router(ticket_event_router)  # 事件下的回应草案生成（T-25）
    protected.include_router(dashboard_router)  # 态势大盘（T-26）
    protected.include_router(report_router)    # 报告中心（T-27）
    protected.include_router(system_router)    # 系统运维（T-28）
    protected.include_router(sse_router)       # SSE 事件流（T-18，注意与 event_router 区分）

    # 后端业务路由到 T-28 为止（T-29~T-36 是前端页面与贯通联调）
    return public, protected


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        description="面向企业品牌与公共事件的智能舆情分析平台",
        lifespan=lifespan,
    )

    # 中间件顺序：CORS 在外、trace 在内——trace 要给每个响应（含预检之外的真实
    # 请求）补 X-Trace-Id，而 CORS 负责让浏览器能看到这个头
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )
    app.add_middleware(TraceIdMiddleware)

    register_error_handlers(app)

    # 健康检查注册在根路径且不需要认证（探针用，不能带凭据）
    app.include_router(health.router)

    public_router, protected_router = _build_routers()
    app.include_router(public_router)
    app.include_router(protected_router)

    return app


app = create_app()
