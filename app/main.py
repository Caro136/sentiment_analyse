"""FastAPI 应用装配。

职责边界：只做「创建实例、挂载中间件、注册路由、管理生命周期」。
业务逻辑一律下沉到 `app/services/` 与 `engines/`——接入层保持轻量。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import health
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
    yield
    logger.info("{} 已关闭", APP_TITLE)


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title=APP_TITLE,
        version=APP_VERSION,
        description="面向企业品牌与公共事件的智能舆情分析平台",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)

    # 业务路由将按任务逐步注册：
    #   T-19 认证  T-21 监测配置  T-22 内容查询  T-23 事件与溯源
    #   T-24 预警  T-25 处置工单  T-26 态势大盘  T-27 报告中心
    return app


app = create_app()
