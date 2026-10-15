"""监测配置服务（T-21，对应 PRD FR-06~FR-13）。

模块划分：

| 模块 | 职责 |
|------|------|
| `repository`     | `topic` / `topic_keyword` / `source_channel` / `topic_channel` 的读写 |
| `topics`         | 主题与词包用例（含主题关键词的推导与跟随规则） |
| `channels`       | 通道启停与调参、连通性自检（FR-13） |
| `runtime_config` | 运行配置读取与热更新（白名单，FR-53） |

接口注册在 `app/routers/{topics,channels,config}.py`；权限点见
`engines/contracts/permissions.py`（`topic:write` 对分析师只有本人主题，
数据范围判定由本层调用 `ensure_data_scope`）。

未纳入本任务：FR-10 主题配置导入导出（P2）、预警规则与通知渠道
（`alert_rule` 属 T-24，监测配置页的另两个页签）。
"""

from app.services.monitoring.channels import (
    check_channels,
    ensure_default_channels,
    list_channels,
    update_channel,
)
from app.services.monitoring.topics import (
    create_topic,
    get_topic,
    list_topics,
    replace_channels,
    replace_keywords,
    update_topic,
)

__all__ = [
    "create_topic", "get_topic", "list_topics", "update_topic",
    "replace_keywords", "replace_channels",
    "list_channels", "update_channel", "check_channels", "ensure_default_channels",
]
