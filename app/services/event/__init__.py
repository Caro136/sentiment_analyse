"""事件与溯源服务（T-23，对应 PRD FR-45~FR-48）。

| 模块 | 职责 |
|------|------|
| `repository` | `event` / `event_content` / `opinion_cluster` / `cluster_member` 的**只读**查询（图谱复用 analytics） |
| `service`    | 统计聚合、溯源角色推导、时间线与聚类视图组装（内容与标签的读取复用 T-22 的服务） |

**几乎全是读操作**：事件的建立与标签归 analytics，内容归接入层——查询接口不该顺手改数据。
唯一的写入是**风险等级人工复核**（FR-24），它是业务上确实存在的动作，
落库时带审计（`AuditAction.RISK_OVERRIDE`）。

两点需要留意（详见 `service` 模块文档）：

- 事件的统计与风险等级是**读取时现算**的（`event` 表的聚合列是空的）；
  人工复核后就以人工值为准，算法结论仍在响应里给出（双份记录）；
- 溯源节点角色是**推导**的（`spread_node` 表为空），响应里以 `role_source=derived` 标注。
"""

from app.services.event.service import (
    build_timeline,
    get_event_detail,
    list_cluster_members,
    list_clusters,
    list_events,
    override_risk_level,
)

__all__ = [
    "list_events",
    "get_event_detail",
    "override_risk_level",
    "build_timeline",
    "list_clusters",
    "list_cluster_members",
]
