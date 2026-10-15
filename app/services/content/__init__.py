"""内容查询服务（T-22，对应 PRD FR-42~FR-44）。

| 模块 | 职责 |
|------|------|
| `repository` | `content_analysis` 与 `topic` 的**只读**查询 |
| `service`    | 多维筛选、分页、原文与多模态证据的组装 |

原始表与多模态产物的读取复用接入层（`engines.ingestion.reader` /
`modality.repository`），本层不重复实现列名映射——那正是接入层存在的原因。

排序与分页的取舍见 `service` 模块文档（发布时间倒序、应用层分页）。
"""

from app.services.content.service import get_content_detail, list_contents

__all__ = ["list_contents", "get_content_detail"]
