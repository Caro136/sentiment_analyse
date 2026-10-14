"""服务启动入口。

用法：
    python main.py                # 按 .env 中的 HOST / PORT 启动
    uvicorn app.main:app --reload  # 开发期热重载

⚠️ **必须单 worker 运行**（设计规格 ADR-05）：
事件总线与主持人会话是进程内状态，多 worker 会导致事件只到达持有订阅者的那个进程，
表现为「进度条卡住不动」「研判永不触发」。因此这里显式写死 workers=1。
若将来需要多副本部署，须先把 common/eventing 替换为消息队列实现。
"""

import uvicorn

from engines.contracts.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        workers=1,          # 见模块文档：单 worker 是硬约束
        log_config=None,    # 日志交给我们自己的 loguru + InterceptHandler
    )


if __name__ == "__main__":
    main()
