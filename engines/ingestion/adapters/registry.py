"""适配器注册表。

按信源类型取适配器，按表名反查适配器（清理与同步任务拿到的是表名而非信源类型）。

新增信源时只需实现适配器并在此登记——上层代码一律通过本模块获取，
不直接 import 具体适配器。
"""

from engines.contracts.enums import SourceType

from .base import SourceAdapter
from .douyin import DouyinAdapter
from .forum import ForumAdapter
from .news import NewsAdapter
from .weibo import WeiboAdapter

# 适配器实例无状态，模块级共享即可
_ADAPTERS: dict[SourceType, SourceAdapter] = {
    SourceType.SOCIAL: WeiboAdapter(),
    SourceType.VIDEO: DouyinAdapter(),
    SourceType.NEWS: NewsAdapter(),
    SourceType.FORUM: ForumAdapter(),
}

# 表名 → 适配器。内容表与评论表都要能反查
_TABLE_INDEX: dict[str, SourceAdapter] = {}
for _adapter in _ADAPTERS.values():
    _TABLE_INDEX[_adapter.content_table] = _adapter
    _TABLE_INDEX[_adapter.comment_table] = _adapter


def get_adapter(source_type: SourceType | str) -> SourceAdapter:
    """按信源类型取适配器。

    未知信源一律抛 KeyError——字符串无法转成枚举时抛的是 ValueError，
    但"查不到这个信源"对调用方来说是同一件事，不该有两种异常类型。
    """
    try:
        key = SourceType(source_type) if isinstance(source_type, str) else source_type
        return _ADAPTERS[key]
    except (ValueError, KeyError):
        known = ", ".join(t.value for t in _ADAPTERS)
        raise KeyError(f"未知信源类型 {source_type!r}，已支持：{known}") from None


def adapter_for_table(table_name: str) -> SourceAdapter:
    """按表名取适配器（内容表与评论表均可）。"""
    try:
        return _TABLE_INDEX[table_name]
    except KeyError:
        known = ", ".join(sorted(_TABLE_INDEX))
        raise KeyError(f"表 {table_name!r} 没有对应的适配器，已知表：{known}") from None


def all_adapters() -> tuple[SourceAdapter, ...]:
    """全部适配器，按信源类型固定顺序。"""
    return tuple(_ADAPTERS[t] for t in
                 (SourceType.SOCIAL, SourceType.VIDEO,
                  SourceType.NEWS, SourceType.FORUM))


def content_tables() -> tuple[str, ...]:
    """全部内容表名。"""
    return tuple(a.content_table for a in all_adapters())


def comment_tables() -> tuple[str, ...]:
    """全部评论表名。"""
    return tuple(a.comment_table for a in all_adapters())
