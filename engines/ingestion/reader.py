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
from engines.contracts.content import UnifiedContent, make_content_uid, parse_content_uid
from engines.contracts.enums import SourceType
from engines.ingestion.adapters import all_adapters, get_adapter
from engines.ingestion.adapters.base import to_int

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
    conditions = [_keyword_condition()]
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


def _keyword_condition(prefix: str = "") -> str:
    """主题关键词的匹配条件。

    **双向模糊匹配**，不是精确相等：
    数据集里的 `source_keyword` 是「新能源车购置税」，而调用方往往传更完整的
    「新能源车购置税减免」——精确匹配会一条都召不回，而且**不报错**，
    表现为「该主题没有数据」，很容易被误判成监测盲区。

    两个方向都要覆盖：查询词包含数据里的关键词（前者是后者的细化），
    或数据里的关键词包含查询词（反之）。
    """
    column = f"{prefix}`source_keyword`" if prefix else "`source_keyword`"
    return (f"(:keyword LIKE CONCAT('%', {column}, '%') "
            f"OR {column} LIKE CONCAT('%', :keyword, '%'))")


def _build_comment_sql(adapter, query: FetchQuery) -> tuple[str, dict[str, Any]]:
    """评论通过 JOIN 父内容表取——评论表本身没有主题关键词字段。"""
    conditions = [_keyword_condition("p.")]
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

    # 批量取账号：把内容与评论的查找键合并成一次查询。
    # 评论用 comment_account_lookup_key —— 新闻内容按媒体名匹配，评论按读者的 user_id
    lookup_keys = ([adapter.account_lookup_key(row) for adapter, row in raw_contents]
                   + [adapter.comment_account_lookup_key(row)
                      for adapter, row in raw_comments])
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
        key = adapter.comment_account_lookup_key(row)
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


@dataclass(frozen=True)
class SourceProbe:
    """单个信源的探测结果（FR-13 通道连通性自检）。"""

    source_type: SourceType
    content_table: str
    comment_table: str
    content_rows: int
    comment_rows: int
    latest_published_at: int | None

    @property
    def has_data(self) -> bool:
        return self.content_rows > 0


@dataclass(frozen=True)
class RawHit:
    """原始表里的一条候选内容（只带排序与过滤需要的最小信息）。"""

    content_uid: str
    source_type: SourceType
    source_table: str
    business_id: int
    published_at: int


MATCH_TEXT = "text"
MATCH_TOPIC = "topic"


async def search_raw_hits(*, session: AsyncSession, keyword: str | None = None,
                          match: str = MATCH_TEXT,
                          start_time: int | None = None, end_time: int | None = None,
                          source_types: Sequence[SourceType] = (),
                          include_comments: bool = True) -> list[RawHit]:
    """按「原始侧条件」筛出候选内容，**按发布时间倒序**返回。

    为什么这些条件必须落到原始表：发布时间与正文只存在于原始表
    （分析区只存标签），而「时间跨度筛选」「关键词检索」正是用户在工作台最常用的两个动作。

    典型用法是先拿它限定候选集，再在分析表上叠筛选条件（ADR-03 的两段查询）：
    原始侧决定**有哪些内容**，分析侧决定**哪些打了什么标**。

    `match` 决定关键词匹配的是**哪个字段**，两者语义完全不同，不能混用：

    | 取值 | 匹配字段 | 用途 |
    |------|---------|------|
    | `text`（默认） | 正文列（微博 content、抖音 desc…） | 工作台的「正文关键词」检索 |
    | `topic` | `source_keyword` 列，**双向模糊** | 判定内容归属哪个监测主题（FR-08） |

    写错这一个参数的表现是「筛选结果少得离谱」而不报错——例如用正文检索去找
    主题归属，会把「正文里恰好提到该词」的内容算成命中，而真正属于该主题的内容漏掉。

    规模提示：本函数不做分页，返回的是完整候选集（演示规模千级）。若数据量继续增长，
    应把 `published_at` 冗余进 `content_analysis`（需要改 DDL），恢复纯 SQL 分页。
    """
    if match not in (MATCH_TEXT, MATCH_TOPIC):
        raise ValueError(f"未知的匹配方式：{match!r}")

    adapters = (all_adapters() if not source_types
                else tuple(get_adapter(t) for t in source_types))

    hits: list[RawHit] = []
    for adapter in adapters:
        # --- 内容表 ---
        conditions: list[str] = []
        params: dict[str, Any] = {}
        if keyword:
            if match == MATCH_TOPIC:
                # 与 reader 的主题归属判定同一套条件（双向模糊）
                conditions.append(_keyword_condition())
            else:
                like_terms = [f"`{field}` LIKE :kw" for field in adapter.search_fields]
                if like_terms:
                    conditions.append("(" + " OR ".join(like_terms) + ")")
            params["keyword"] = keyword
            if match == MATCH_TEXT:
                params["kw"] = f"%{keyword}%"
        if start_time is not None:
            conditions.append(f"`{adapter.published_field}` >= :start_time")
            params["start_time"] = start_time
        if end_time is not None:
            conditions.append(f"`{adapter.published_field}` <= :end_time")
            params["end_time"] = end_time

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = (f"SELECT `{adapter.content_business_field}` AS bid, "
               f"`{adapter.published_field}` AS published "
               f"FROM `{adapter.content_table}` {where}")
        rows = await _fetch_rows(session, sql, params)

        parent_ids: list[int] = []
        for row in rows:
            business_id = to_int(row.get("bid"))
            if not business_id:
                continue
            parent_ids.append(business_id)
            hits.append(RawHit(
                content_uid=make_content_uid(adapter.source_type, adapter.content_table,
                                             business_id),
                source_type=adapter.source_type,
                source_table=adapter.content_table,
                business_id=business_id,
                published_at=to_int(row.get("published")),
            ))

        # --- 评论表 ---
        if not include_comments:
            continue
        comment_conditions: list[str] = []
        comment_params: dict[str, Any] = {}
        if keyword and match == MATCH_TEXT:
            like_terms = [f"`{field}` LIKE :kw"
                          for field in adapter.comment_search_fields]
            if like_terms:
                comment_conditions.append("(" + " OR ".join(like_terms) + ")")
                comment_params["kw"] = f"%{keyword}%"
        elif match == MATCH_TOPIC:
            # 评论表没有 source_keyword：归属由父内容决定，只取父内容命中的那些评论
            if not parent_ids:
                continue
            placeholders = ", ".join(f":p{i}" for i in range(len(parent_ids)))
            comment_conditions.append(f"`{adapter.parent_field}` IN ({placeholders})")
            comment_params.update({f"p{i}": v for i, v in enumerate(parent_ids)})
        if start_time is not None:
            comment_conditions.append(
                f"`{adapter.comment_published_field}` >= :start_time")
            comment_params["start_time"] = start_time
        if end_time is not None:
            comment_conditions.append(
                f"`{adapter.comment_published_field}` <= :end_time")
            comment_params["end_time"] = end_time

        comment_where = (f"WHERE {' AND '.join(comment_conditions)}"
                         if comment_conditions else "")
        comment_sql = (f"SELECT `{adapter.comment_business_field}` AS bid, "
                       f"`{adapter.comment_published_field}` AS published "
                       f"FROM `{adapter.comment_table}` {comment_where}")
        for row in await _fetch_rows(session, comment_sql, comment_params):
            business_id = to_int(row.get("bid"))
            if not business_id:
                continue
            hits.append(RawHit(
                content_uid=make_content_uid(adapter.source_type, adapter.comment_table,
                                             business_id),
                source_type=adapter.source_type,
                source_table=adapter.comment_table,
                business_id=business_id,
                published_at=to_int(row.get("published")),
            ))

    # 跨表合并后统一按发布时间倒序：四张表各查一次，顺序只能在应用层合并
    hits.sort(key=lambda hit: hit.published_at, reverse=True)
    return hits


async def load_contents(uids: Sequence[str],
                        session: AsyncSession) -> dict[str, UnifiedContent]:
    """按统一标识批量取回内容（含账号权重），供详情与列表回填原文。

    一次请求的列表页可能有几十条内容、分属四类信源，因此按表分组后各发一条
    `IN` 查询，账号也是一次批量取（N+1 会让列表接口直接退化）。
    识别不了的标识**直接跳过**——分析表里可能残留原始表已清理的记录，
    这种情况应当少一条内容，而不是整个列表报错。
    """
    if not uids:
        return {}

    buckets: dict[tuple[str, str], list[int]] = {}
    for uid in uids:
        try:
            source_type, table, business_id = parse_content_uid(uid)
        except ValueError:
            logger.warning("跳过无法解析的 content_uid：{}", uid)
            continue
        buckets.setdefault((source_type, table), []).append(business_id)

    contents: dict[str, UnifiedContent] = {}
    for (source_type_value, table), business_ids in buckets.items():
        try:
            adapter = get_adapter(SourceType(source_type_value))
        except (KeyError, ValueError):
            logger.warning("跳过未知信源的 content_uid：{}:{}", source_type_value, table)
            continue

        is_comment = table == adapter.comment_table
        business_field = (adapter.comment_business_field if is_comment
                          else adapter.content_business_field)

        rows: list[dict[str, Any]] = []
        for start in range(0, len(business_ids), ACCOUNT_BATCH_SIZE):
            batch = business_ids[start:start + ACCOUNT_BATCH_SIZE]
            placeholders = ", ".join(f":b{i}" for i in range(len(batch)))
            params = {f"b{i}": v for i, v in enumerate(batch)}
            sql = (f"SELECT * FROM `{table}` WHERE `{business_field}` IN ({placeholders})")
            rows.extend(await _fetch_rows(session, sql, params))

        # 评论与内容的账号查键不同（新闻内容按媒体名、评论按读者 user_id）
        lookup = (adapter.comment_account_lookup_key if is_comment
                  else adapter.account_lookup_key)
        accounts = await load_accounts(session, [lookup(row) for row in rows])

        for row in rows:
            account = accounts.get(lookup(row))
            content = (adapter.to_comment(row, account=account) if is_comment
                       else adapter.to_content(row, account=account))
            contents[content.content_uid] = content

    return contents


async def probe_source(source_type: SourceType,
                       session: AsyncSession) -> SourceProbe:
    """统计某信源原始表的条数与最新发布时间（FR-13）。

    只发 SELECT（ADR-03：原始表只读）。用途是通道自检——**能连上库、表里有数据、
    数据的时间戳是新的**，这三件事合起来才是「通道可用」的可用信号；
    本项目没有真实采集器，因此不去探测外部站点（那会给出一个假的「通道正常」）。
    """
    adapter = get_adapter(source_type)
    sql = (f"SELECT COUNT(*) AS n, MAX(`{adapter.published_field}`) AS latest "
           f"FROM `{adapter.content_table}`")
    row = (await session.execute(text(sql), {})).mappings().first()
    content_rows = int((row or {}).get("n") or 0)
    latest = (row or {}).get("latest")
    latest_published_at = int(latest) if latest is not None else None

    comment_row = (await session.execute(
        text(f"SELECT COUNT(*) AS n FROM `{adapter.comment_table}`"), {})
    ).mappings().first()
    comment_rows = int((comment_row or {}).get("n") or 0)

    return SourceProbe(
        source_type=source_type,
        content_table=adapter.content_table,
        comment_table=adapter.comment_table,
        content_rows=content_rows,
        comment_rows=comment_rows,
        latest_published_at=latest_published_at,
    )
