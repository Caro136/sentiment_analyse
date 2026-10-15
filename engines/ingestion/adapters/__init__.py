"""平台适配器：把四类异构原始表映射为统一的 UnifiedContent。

上层一律通过 `registry` 获取适配器，不直接 import 具体实现——
这样新增信源只需实现适配器并登记，调用方无需改动。
"""

from engines.ingestion.adapters.base import SourceAdapter, to_int, to_str
from engines.ingestion.adapters.registry import (
    adapter_for_table,
    all_adapters,
    comment_tables,
    content_tables,
    get_adapter,
)

__all__ = [
    "SourceAdapter",
    "to_int",
    "to_str",
    "get_adapter",
    "adapter_for_table",
    "all_adapters",
    "content_tables",
    "comment_tables",
]
