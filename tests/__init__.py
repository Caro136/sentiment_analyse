"""单元测试。

运行：python -m unittest discover -s tests -v

纯标准库 unittest，不需要安装 pytest——本项目依赖尽量精简，
测试是随时可执行的保障手段，不应成为额外的环境负担。

需要看日志时加环境变量：TEST_VERBOSE=1 python -m unittest discover
"""

import os

from loguru import logger

# 默认关闭日志：重试器、降级策略的测试本身就会产生大量 WARNING/ERROR，
# 会把测试结果淹没。这些日志是被验证的行为，不是异常。
if not os.environ.get("TEST_VERBOSE"):
    logger.remove()

