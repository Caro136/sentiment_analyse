"""态势大盘服务（T-26，对应 PRD FR-36~FR-41）。

| 模块 | 职责 |
|------|------|
| `metrics`    | 五个指标的**纯函数**计算（可脱离数据库测试） |
| `repository` | 聚合取数 + `dashboard_metrics` 的 upsert/读取 |
| `service`    | 读路径（缺指标或过期就同步补算）与刷新编排 |
| `refresher`  | 后台定时任务（每 60 秒一轮，随应用生命周期启停） |
| `refresh`    | 手动刷新 CLI（补算与排障） |

**物化而非请求内聚合**（设计规格 8.2）：读路径只做一次索引查询，
聚合成本摊给后台任务；同时读路径自愈——指标缺失或过期时同步补算一次，
避免「服务刚起来、大盘一屏空图」。

告警滚动列表（FR-38）**不在这里**：它直接走 `/api/v1/alerts`（实时性优先，
`alert_record` 上有状态+时间索引），重复物化一份只会制造第二个事实源。
"""

from app.services.dashboard.refresher import DashboardRefresher, get_refresher
from app.services.dashboard.service import (
    METRIC_KEYS,
    get_metrics,
    refresh_all,
    refresh_scope,
)

__all__ = [
    "get_metrics", "refresh_scope", "refresh_all", "METRIC_KEYS",
    "DashboardRefresher", "get_refresher",
]
