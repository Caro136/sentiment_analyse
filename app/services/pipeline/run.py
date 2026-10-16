"""监测→分析链路的编排实现与命令行入口（T-34）。

六个阶段，顺序不能颠倒：

```
接入（读原始表 → 清洗 → 去重 → 水军识别 → [多模态]）
  → 认知分析打标（情感 / 情绪 / 烈度 / 风险）
  → 事件关联（一个主题一个事件专题）
  → 实体图谱 / 观点聚类      ← 聚类要消费打标产出的情感标签
  → 向量索引（Milvus）        ← 研判的私域召回依赖它
```

四条实现约定：

1. **整条链路幂等**。打标按 `content_uid` 覆盖、事件与内容关联去重、簇成员先清后写、
   实体按 ID upsert——因此重复跑只补新增内容，不会产生重复记录。
   这一点是「演示可复现」（SC-07）的前提：同一条命令跑几遍结果一致。
2. **局部失败不阻断全局，但要说清**。实体抽取、向量索引失败只记进 `warnings`
   并继续（研判还能用关键词兜底）；只有「接入一条都没取到」才提前结束——
   后面几步都没有输入。失败原因原样带出，不做「看起来成功」的粉饰。
3. **LLM 与向量编码在事务之外**。打标、实体抽取、聚类、编码都是秒级到分钟级的
   外部调用，放进事务里会长时间占着连接。因此按「先算后写」分两段：
   先在事务外算出结果，再开短事务落库。
4. **停用主题默认拒绝**。停用意味着「不再纳入监测」，继续往里拉数据与使用者的
   意图相反——确实要跑时用 `--force` 显式覆盖。
"""

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field

from loguru import logger

from app.services.monitoring.repository import SqlMonitoringRepository, TopicRecord
from engines.analytics import repository as analytics_repo
from engines.analytics.clustering import ClusteringResult, cluster_texts
from engines.analytics.entities import ExtractionResult, extract_entities
from engines.analytics.indexing import IndexResult, index_contents
from engines.analytics.service import AnalysisStats, analyze_contents
from engines.common.db.session import session_scope
from engines.common.runtime.concurrency import DEFAULT_LIMIT
from engines.ingestion.pipeline import IngestionStats, ingest
from engines.ingestion.reader import FetchQuery

__all__ = ["PipelineReport", "run_pipeline", "resolve_topic", "main"]


@dataclass
class PipelineReport:
    """一次链路运行的产出与统计。

    字段刻意给全（而不是只给一个「成功」）：联调时要能回答
    「为什么这个主题的事件里没有内容」「为什么检索不到」这类问题。
    """

    keyword: str
    topic_id: str | None = None
    topic_name: str = ""
    window_hours: int | None = None
    ingestion: IngestionStats | None = None
    analysis: AnalysisStats | None = None
    event_id: str = ""
    attached: int = 0
    cluster_count: int = 0
    cluster_degraded: str = ""
    entity_count: int = 0
    relation_count: int = 0
    entity_failed: int = 0
    indexed: IndexResult | None = None
    warnings: list[str] = field(default_factory=list)
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        """链路是否产出了可用于研判的结果（取到内容 + 有关联事件）。"""
        return bool(self.event_id) and bool(self.ingestion and self.ingestion.fetched_total)

    def render(self) -> str:
        lines = [
            f"链路运行完成：主题「{self.topic_name or '（未绑定主题）'}」"
            f"关键词「{self.keyword}」 耗时 {self.duration:.1f}s",
        ]
        if self.ingestion is not None:
            lines.append("  " + self.ingestion.render().replace("\n", "\n  "))
        if self.analysis is not None:
            lines.append("  " + self.analysis.render())
        lines.append(
            f"  事件 {self.event_id or '-'}：新关联内容 {self.attached} 条")
        lines.append(
            f"  观点簇 {self.cluster_count} 个"
            + (f"（降级：{self.cluster_degraded}）" if self.cluster_degraded else ""))
        lines.append(
            f"  实体 {self.entity_count} 个 / 关系 {self.relation_count} 条"
            + (f"（{self.entity_failed} 条内容抽取失败）" if self.entity_failed else ""))
        if self.indexed is not None:
            lines.append("  " + self.indexed.render().replace("\n", " "))
        for warning in self.warnings:
            lines.append(f"  ⚠ {warning}")
        return "\n".join(lines)


async def resolve_topic(*, topic_id: str | None = None, topic_name: str | None = None,
                        keyword: str | None = None) -> tuple[TopicRecord | None, str]:
    """解析目标主题，返回 (主题记录, 接入用的关键词)。

    接入靠 `source_keyword` 做归属匹配（不是靠主题名），因此这里必须取主题的关键词；
    主题关键词为空的主题（建主题时既没给词包也没给关键词）**接不到任何内容**——
    这种情况明确报错而不是静默跑出一个空结果。
    """
    if topic_id is None and topic_name is None:
        if not keyword:
            raise ValueError("必须提供 --topic 或 --keyword 之一")
        return None, keyword

    target = topic_id or topic_name
    async with session_scope() as session:
        repo = SqlMonitoringRepository(session)
        # `--topic` 允许给 topic_id 或名称：先按 ID 查，查不到再按名称**精确**匹配
        # （模糊匹配会在「星耀」与「星耀XPhone15」之间选错主题）
        topic = await repo.get_topic(target)
        if topic is None:
            _, candidates = await repo.list_topics(page=1, page_size=50, keyword=target)
            topic = next((t for t in candidates if t.name == target), None)
        if topic is None:
            raise ValueError(f"主题不存在：{target}")

    effective = topic.source_keyword or keyword
    if not effective:
        raise ValueError(
            f"主题「{topic.name}」没有主题关键词（source_keyword），接入层无法归属内容；"
            f"请先在监测配置里补词包（保存词包会按品牌词 → 事件词 → 竞品词推导）")
    return topic, effective


async def run_pipeline(*, topic_id: str | None = None, topic_name: str | None = None,
                       keyword: str | None = None, window_hours: int | None = None,
                       limit_per_table: int | None = None,
                       resolve_media: bool = False, persist_modality: bool = False,
                       with_entities: bool = True, with_clusters: bool = True,
                       with_index: bool = True, force: bool = False,
                       max_concurrency: int = DEFAULT_LIMIT) -> PipelineReport:
    """跑一次「监测 → 分析」链路。

    `resolve_media=False` 是默认值：数据集里的图片/视频是占位 URL，OCR 必然失败，
    开着只是白花外部调用与时间（需要验证多模态时用 `--with-media` 显式打开）。
    """
    started = time.monotonic()
    topic, effective = await resolve_topic(topic_id=topic_id, topic_name=topic_name,
                                          keyword=keyword)
    if topic is not None and topic.status != "enabled" and not force:
        raise ValueError(
            f"主题「{topic.name}」已停用，接入会与「不再监测」的意图相反；"
            f"确认要跑请加 --force")

    report = PipelineReport(
        keyword=effective,
        topic_id=topic.topic_id if topic else None,
        topic_name=topic.name if topic else "",
        window_hours=window_hours,
    )
    logger.info("链路开始：主题={} 关键词={} 时间窗={} 小时",
                report.topic_name or "-", effective, window_hours or "不限")

    # ---- 1. 接入 ----
    query = FetchQuery(
        keyword=effective,
        start_time=int(time.time()) - window_hours * 3600 if window_hours else None,
        limit_per_table=limit_per_table,
    )
    ingestion = await ingest(query, resolve_media=resolve_media,
                             persist_modality=persist_modality)
    report.ingestion = ingestion.stats
    if not ingestion.contents:
        report.warnings.append("接入没有取到任何内容，后续阶段跳过（检查主题关键词与时间窗）")
        report.duration = time.monotonic() - started
        return report

    # ---- 2. 打标（事务外；已打标的自动跳过，因此重复跑很便宜）----
    analysis = await analyze_contents(
        ingestion.contents,
        fingerprints=ingestion.fingerprints,
        bot_account_ids=ingestion.bot_account_ids,
        topic_id=report.topic_id,
        max_concurrency=max_concurrency,
        persist=True,
    )
    report.analysis = analysis.stats
    if analysis.failed_uids:
        report.warnings.append(
            f"{len(analysis.failed_uids)} 条内容打标失败（已按零置信度中性落库，可重跑补齐）")

    labels: dict[str, dict] = {}
    async with session_scope() as session:
        # sync_title：事件标题必须与研判主题词一致，否则「从事件发起报告」找不到三方产物
        event_id = await analytics_repo.ensure_event(
            session, effective, title=topic.name if topic else effective,
            topic_id=report.topic_id or effective, sync_title=True)
        attached = await analytics_repo.attach_contents(
            session, event_id, [content.content_uid for content in ingestion.contents])
        labels = await analytics_repo.fetch_labels(
            session, [content.content_uid for content in ingestion.contents])
    report.event_id = event_id
    report.attached = attached

    # ---- 3. 实体抽取与观点聚类（都在事务外；聚类消费打标产出的情感）----
    extraction = None
    if with_entities:
        extraction = await extract_entities(ingestion.contents)
        report.entity_failed = len(extraction.failed)

    clusters: ClusteringResult | None = None
    if with_clusters:
        sentiments = {uid: (label.get("sentiment") or "")
                      for uid, label in labels.items()}
        clusters = await cluster_texts(
            [(content.content_uid, content.display_text) for content in ingestion.contents],
            sentiments=sentiments)
        report.cluster_degraded = clusters.degraded_reason

    async with session_scope() as session:
        if extraction is not None:
            report.entity_count, report.relation_count = await analytics_repo.save_entities(
                session, extraction, event_id=event_id)
        if clusters is not None:
            report.cluster_count = await analytics_repo.save_clusters(
                session, clusters.clusters, scope=effective, event_id=event_id,
                topic_id=report.topic_id)

    # ---- 4. 向量索引（研判的私域召回依赖它）----
    if with_index:
        indexed = await index_contents(ingestion.contents)
        report.indexed = indexed
        if indexed.error:
            report.warnings.append(f"向量索引失败：{indexed.error}（研判将降级为关键词召回）")

    report.duration = time.monotonic() - started
    logger.info("链路完成：主题={} 耗时 {:.1f}s 事件={} 簇={} 实体={}",
                report.topic_name or "-", report.duration, report.event_id,
                report.cluster_count, report.entity_count)
    return report


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.pipeline.run",
        description="跑一次「监测 → 分析」链路（接入 → 打标 → 事件 → 图谱/聚类 → 索引）",
    )
    parser.add_argument("--topic", help="监测主题名称或 topic_id")
    parser.add_argument("--keyword", help="直接给接入用的主题关键词（不绑定主题时使用）")
    parser.add_argument("--window-hours", type=int, default=None,
                        help="只取最近 N 小时的内容，默认不限")
    parser.add_argument("--limit-per-table", type=int, default=None,
                        help="单表条数上限，用于小批量试跑")
    parser.add_argument("--with-media", action="store_true",
                        help="执行多模态解析（默认关闭：占位图片 URL 必然失败，白花外部调用）")
    parser.add_argument("--no-entities", action="store_true", help="跳过实体抽取")
    parser.add_argument("--no-clusters", action="store_true", help="跳过观点聚类")
    parser.add_argument("--no-index", action="store_true", help="跳过向量索引（Milvus）")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_LIMIT,
                        help=f"打标并发上限，默认 {DEFAULT_LIMIT}")
    parser.add_argument("--force", action="store_true", help="主题已停用时仍然执行")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.topic and not args.keyword:
        print("错误：必须提供 --topic 或 --keyword 之一", file=sys.stderr)
        return 2
    try:
        report = asyncio.run(run_pipeline(
            topic_name=args.topic, keyword=args.keyword,
            window_hours=args.window_hours, limit_per_table=args.limit_per_table,
            resolve_media=args.with_media, persist_modality=args.with_media,
            with_entities=not args.no_entities, with_clusters=not args.no_clusters,
            with_index=not args.no_index, force=args.force,
            max_concurrency=args.concurrency,
        ))
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
