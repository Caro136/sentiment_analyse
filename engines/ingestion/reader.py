"""按主题与时间窗从原始表读取内容。

**原始表只读**（设计规格 ADR-03）：本模块只发 SELECT，任何模块都不得对
`weibo_note` / `douyin_aweme` / `news_article` / `forum_thread` 等表做写操作。

两个查询要点：

1. **评论通过 JOIN 内容表取**。评论表没有 `source_keyword` 字段，
   主题归属只能由父内容决定——这也是原始表设计的必然结果（评论本是内容的附属）。
   过滤用的是**内容**的时间而非评论时间，保证事件的完整性：
   一条两周前的内容可能今天还在被评论，那些评论属于同一个事件。
2. **账号信息批量查**。粉丝量与影响力在 `platform_account` 表，
   逐行查询会造成 N+1（1 万条内容等于 1 万次查询），必须批量取回后合并。
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from engines.common.db.session import session_scope
from engines.common.logging import logger
from engines.contracts.content import UnifiedContent
from engines.contracts.enums import SourceType
from engines.ingestion.adapters import all_adapters, get_adapter

ACCOUNT_TABLE = "platform_account"
ACCOUNT_BATCH_SIZE = 500      # IN 子句的参数上限保护


@dataclass(frozen=True)
class FetchQuery:
    """读取条件。"""

    keyword: str                                  # 主题关键词，匹配 source_keyword
    start_time: int | None = None
    end_time: int | None = None
    source_types: tuple[SourceType, ...] = ()     # 空表示全部信源
    include_contents: bool = True
    include_comments: bool = True
    limit_per_table: int | None = None            # 单表上限，None 表示不限

    @property
    def label(self) -> str:
        """用于日志的简短描述。"""
        return f"keyword={self.keyword} 时间窗=[{self.start_time}, {self.end_time}]"


@dataclass
class FetchResult:
    """读取结果，附带来源分布便于排查「某个信源一条都没取到」。"""

    contents: list[UnifiedContent] = field(default_factory=list)
    content_rows: int = 0
    comment_rows: int = 0
    per_source: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.contents)


def _build_content_sql(adapter, query: FetchQuery) -> tuple[str, dict[str, Any]]:
    conditions = ["`source_keyword` = :keyword"]
    params: dict[str, Any] = {"keyword": query.keyword}

    if query.start_time is not None:
        conditions.append(f"`{adapter.published_field}` >= :start_time")
        params["start_time"] = query.start_time
    if query.end_time is not None:
        conditions.append(f"`{adapter.published_field}` <= :end_time")
        params["end_time"] = query.end_time

    sql = (f"SELECT * FROM `{adapter.content_table}` "
           f"WHERE {' AND '.join(conditions)} "
           f"ORDER BY `{adapter.published_field}` DESC")
    if query.limit_per_table:
        sql += f" LIMIT {int(query.limit_per_table)}"
    return sql, params


def _build_comment_sql(adapter, query: FetchQuery) -> tuple[str, dict[str, Any]]:
    """评论通过 JOIN 父内容表取——评论表本身没有主题关键词字段。"""
    conditions = ["p.`source_keyword` = :keyword"]
    params: dict[str, Any] = {"keyword": query.keyword}

    if query.start_time is not None:
        conditions.append(f"p.`{adapter.published_field}` >= :start_time")
        params["start_time"] = query.start_time
    if query.end_time is not None:
        conditions.append(f"p.`{adapter.published_field}` <= :end_time")
        params["end_time"] = query.end_time

    sql = (f"SELECT c.* FROM `{adapter.comment_table}` c "
           f"JOIN `{adapter.content_table}` p "
           f"ON c.`{adapter.parent_field}` = p.`{adapter.content_business_field}` "
           f"WHERE {' AND '.join(conditions)} "
           # 排序按评论自身的时间字段——它与内容表的时间字段未必同名
           f"ORDER BY c.`{adapter.comment_published_field}` DESC")
    if query.limit_per_table:
        sql += f" LIMIT {int(query.limit_per_table)}"
    return sql, params


async def _fetch_rows(session: AsyncSession, sql: str,
                      params: dict[str, Any]) -> list[dict[str, Any]]:
    result = await session.execute(text(sql), params)
    return [dict(row._mapping) for row in result]


async def load_accounts(session: AsyncSession,
                        keys: Sequence[str]) -> dict[str, dict[str, Any]]:
    """批量取账号权重信息。

    返回 {匹配键: 账号行}，匹配键同时包含 account_id 与 nickname——
    新闻门户没有账号 ID，只能按媒体名匹配（见 NewsAdapter.account_lookup_key）。
    """
    unique_keys = [k for k in dict.fromkeys(keys) if k]
    if not unique_keys:
        return {}

    accounts: dict[str, dict[str, Any]] = {}
    for start in range(0, len(unique_keys), ACCOUNT_BATCH_SIZE):
        batch = unique_keys[start:start + ACCOUNT_BATCH_SIZE]
        placeholders = ", ".join(f":k{i}" for i in range(len(batch)))
        params = {f"k{i}": v for i, v in enumerate(batch)}
        sql = (f"SELECT * FROM `{ACCOUNT_TABLE}` "
               f"WHERE `account_id` IN ({placeholders}) "
               f"OR `nickname` IN ({placeholders})")
        result = await session.execute(text(sql), params)
        for row in result:
            data = dict(row._mapping)
            if data.get("account_id"):
                accounts.setdefault(str(data["account_id"]), data)
            if data.get("nickname"):
                accounts.setdefault(str(data["nickname"]), data)
    return accounts


async def fetch_with_session(query: FetchQuery,
                             session: AsyncSession) -> FetchResult:
    """在既有会话中读取（供需要与写操作共享事务的调用方使用）。"""
    adapters = (all_adapters() if not query.source_types
                else tuple(get_adapter(t) for t in query.source_types))

    raw_contents: list[tuple[Any, dict[str, Any]]] = []
    raw_comments: list[tuple[Any, dict[str, Any]]] = []

    for adapter in adapters:
        if query.include_contents:
            sql, params = _build_content_sql(adapter, query)
            for row in await _fetch_rows(session, sql, params):
                raw_contents.append((adapter, row))
        if query.include_comments:
            sql, params = _build_comment_sql(adapter, query)
            for row in await _fetch_rows(session, sql, params):
                raw_comments.append((adapter, row))

    # 批量取账号：把内容与评论的查找键合并成一次查询
    lookup_keys = [adapter.account_lookup_key(row)
                   for adapter, row in (*raw_contents, *raw_comments)]
    accounts = await load_accounts(session, lookup_keys)

    result = FetchResult(content_rows=len(raw_contents),
                         comment_rows=len(raw_comments))

    for adapter, row in raw_contents:
        key = adapter.account_lookup_key(row)
        content = adapter.to_content(row, account=accounts.get(key))
        result.contents.append(content)
        result.per_source[adapter.content_table] = \
            result.per_source.get(adapter.content_table, 0) + 1

    for adapter, row in raw_comments:
        key = adapter.account_lookup_key(row)
        content = adapter.to_comment(row, account=accounts.get(key))
        result.contents.append(content)
        result.per_source[adapter.comment_table] = \
            result.per_source.get(adapter.comment_table, 0) + 1

    return result


async def fetch_contents(query: FetchQuery) -> FetchResult:
    """按条件读取内容与评论，映射为统一内容模型。

    用法：
        result = await fetch_contents(FetchQuery(keyword="星耀XPhone15",
                                                 start_time=..., end_time=...))
        for content in result.contents: ...
    """
    async with session_scope() as session:
        result = await fetch_with_session(query, session)

    logger.info(
        "接入完成 {} | 内容 {} 条 / 评论 {} 条 | 分布 {}",
        query.label, result.content_rows, result.comment_rows, result.per_source)
    return result
