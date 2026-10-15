"""操作审计（T-20，对应 PRD FR-04 / 设计规格 8.1）。

覆盖范围：登录（含失败）、用户管理（新增 / 改角色状态 / 重置口令）已接；
配置变更、风险复核、工单流转、报告生成与导出、告警认领、数据重置等动作的
取值已在 `AuditAction` 中登记，由 T-21~T-28 在其写接口上挂 `@audit` 即可
——**审计模块本身不需要再改**。

模块划分：

| 模块 | 职责 |
|------|------|
| `context`  | 操作人 / 来源 IP / 前后值的请求级上下文（contextvar） |
| `writer`   | 落库与查询（`audit_log` 的唯一读写入口，独立事务） |
| `decorator`| `@audit(action, target=...)`，成功与失败都留痕 |

最常用的一行：

```python
from app.services.audit import audit, note_change
from engines.contracts.enums import AuditAction

@audit(AuditAction.CONFIG_UPDATE, target="topic:{topic_id}")
async def update_topic(repo, *, topic_id, ...):
    ...
    note_change(before=old, after=new)
```

留给 T-28 的部分：审计记录的**检索接口**（FR-72~75 的系统运维页）。
查询能力（`writer.list_entries`，支持动作/操作人/时间范围 + 分页）已就绪，
T-28 只需挂一个路由。
"""

from app.services.audit.context import note_change
from app.services.audit.decorator import audit
from app.services.audit.writer import list_entries, write_entry

__all__ = ["audit", "note_change", "write_entry", "list_entries"]
