"""健康检查路由。

`/healthz` 存活探测：进程是否活着，永远快速返回。
`/readyz`  就绪探测：依赖是否可达，供联调与排障使用。

就绪探测用 TCP 连通性判断，不引入数据库驱动依赖——这样在 T-02 数据层尚未落地时
本接口就已可用，能第一时间回答"连不上 MySQL 还是服务本身的问题"。
"""

import socket

from fastapi import APIRouter

from engines.contracts.config import get_settings

router = APIRouter(tags=["health"])


def _tcp_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@router.get("/healthz", summary="存活探测")
async def healthz() -> dict:
    return {"status": "ok", "service": "sentiment-analyse"}


@router.get("/readyz", summary="就绪探测：依赖连通性与配置完整度")
async def readyz() -> dict:
    settings = get_settings()

    dependencies = [
        {
            "name": "MySQL",
            "target": f"{settings.db_host}:{settings.db_port}",
            "reachable": _tcp_reachable(settings.db_host, settings.db_port),
        },
    ]

    if settings.insight_vector_enabled:
        milvus_host, milvus_port = _split_host_port(settings.milvus_uri, default_port=19530)
        dependencies.append({
            "name": "Milvus",
            "target": f"{milvus_host}:{milvus_port}",
            "reachable": _tcp_reachable(milvus_host, milvus_port),
        })

    capabilities = [
        {"name": name, "ready": ready, "detail": detail}
        for name, ready, detail in settings.startup_diagnostics()
    ]

    dependencies_ready = all(d["reachable"] for d in dependencies)
    return {
        "status": "ok" if dependencies_ready else "degraded",
        "dependencies": dependencies,
        "capabilities": capabilities,
    }


def _split_host_port(uri: str, default_port: int = 19530) -> tuple[str, int]:
    """从 `http://host:port` 形态的 URI 中解析主机与端口。"""
    text = uri.split("://", 1)[-1].rstrip("/")
    if ":" in text:
        host, _, port_text = text.partition(":")
        try:
            return host, int(port_text)
        except ValueError:
            return host, default_port
    return text, default_port
