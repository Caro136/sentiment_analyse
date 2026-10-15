"""单元测试。

运行：python -m unittest discover -s tests -v

纯标准库 unittest，不需要安装 pytest——本项目依赖尽量精简，
测试是随时可执行的保障手段，不应成为额外的环境负担。

需要看日志时加环境变量：TEST_VERBOSE=1 python -m unittest discover
"""

import os
from unittest.mock import patch

from loguru import logger

if not os.environ.get("TEST_VERBOSE"):
    # 关掉 `app.main.create_app()` 里的日志初始化。注意顺序：`app/main.py` 末尾有
    # 模块级 `app = create_app()`（uvicorn 的入口约定，必须保留），所以**导入 app.main
    # 本身就会建好 7 个 sink**（6 个文件 sink 各带一个 enqueue 线程）——
    # 执行顺序必须是「先补丁 → 再导入 → 最后清 sink」，否则测试跑起来每条日志
    # 都要扇到 7 个 sink，整套用例平白慢上几十秒。
    _setup_logging_patch = patch("app.main.setup_logging")
    _setup_logging_patch.start()

    import app.main  # noqa: F401  触发上面的补丁生效后再清 sink

    # 默认关闭日志：重试器、降级策略的测试本身就会产生大量 WARNING/ERROR，
    # 会把测试结果淹没。这些日志是被验证的行为，不是异常。
    logger.remove()

