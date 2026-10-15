"""接入流程编排：读取 → 清洗 → 去重 → 多模态解析。

把 T-07~T-09 的三段串成一条链，产出可供 `analytics` 打标的干净内容流。

顺序不能颠倒：

    先去重再解析多模态，代价完全不同
    ——重复内容解析出来的文本也是重复的，先合并可以把 OCR 调用量降下来。
    反过来（先解析再去重）会在重复内容上白花外部调用。

清洗放在去重**之前**：清洗会去掉 URL、零宽字符这类噪声，
水军正是靠这些制造"看起来不同"的变体，不先清洗，去重会漏掉相当一部分。
"""

from dataclasses import dataclass, field, replace

from sqlalchemy.ext.asyncio import AsyncSession

from engines.common.db.session import session_scope
from engines.common.logging import logger
from engines.contracts.content import UnifiedContent
from engines.ingestion.cleaning import clean_text, has_meaningful_text
from engines.ingestion.dedup import BotSignal, deduplicate, detect_bots
from engines.ingestion.modality.service import ModalityResolution, resolve_modality
from engines.ingestion.reader import (
    FetchQuery,
    FetchResult,
    fetch_with_session,
)


@dataclass
class IngestionStats:
    """接入各环节的统计，用于日志、看板与问题排查。"""

    fetched_contents: int = 0
    fetched_comments: int = 0
    unique_count: int = 0
    duplicate_count: int = 0
    bot_accounts: int = 0
    no_meaningful_text: int = 0          # 纯表情/纯标点，无文本分析价值
    modality_total: int = 0
    modality_succeeded: int = 0
    modality_skipped: int = 0

    @property
    def fetched_total(self) -> int:
        return self.fetched_contents + self.fetched_comments

    @property
    def duplicate_ratio(self) -> float:
        return self.duplicate_count / self.fetched_total if self.fetched_total else 0.0

    @property
    def modality_failed(self) -> int:
        return self.modality_total - self.modality_succeeded

    def render(self) -> str:
        lines = [
            f"接入 {self.fetched_total} 条（内容 {self.fetched_contents} / 评论 {self.fetched_comments}）",
            f"去重后 {self.unique_count} 条，合并同质内容 {self.duplicate_count} 条"
            f"（{self.duplicate_ratio:.1%}）",
            f"疑似水军账号 {self.bot_accounts} 个",
            f"无实质文本（纯表情/标点）{self.no_meaningful_text} 条",
        ]
        if self.modality_total:
            lines.append(
                f"多模态资产 {self.modality_total} 个："
                f"成功 {self.modality_succeeded} / 失败 {self.modality_failed}"
                f"，另有 {self.modality_skipped} 条内容无媒体资源")
        return "\n".join(lines)


@dataclass
class IngestionResult:
    """接入产出的完整结果。"""

    contents: list[UnifiedContent] = field(default_factory=list)
    duplicate_of: dict[str, str] = field(default_factory=dict)
    fingerprints: dict[str, int] = field(default_factory=dict)
    bot_signals: list[BotSignal] = field(default_factory=list)
    modality: ModalityResolution | None = None
    stats: IngestionStats = field(default_factory=IngestionStats)
    per_source: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.contents)

    @property
    def bot_account_ids(self) -> set[str]:
        return {signal.account_id for signal in self.bot_signals}


def _apply_cleaning(contents: list[UnifiedContent]) -> list[UnifiedContent]:
    """清洗正文。原文保留在 raw_text，展示与溯源时仍可看到未清洗内容。"""
    cleaned: list[UnifiedContent] = []
    for content in contents:
        text = clean_text(content.raw_text or content.text)
        if text == content.text:
            cleaned.append(content)
        else:
            cleaned.append(replace(content, text=text))
    return cleaned


async def _ingest(query: FetchQuery, session: AsyncSession, *,
                  resolve_media: bool, persist_modality: bool) -> IngestionResult:
    stats = IngestionStats()

    # 1. 读取
    fetched: FetchResult = await fetch_with_session(query, session)
    stats.fetched_contents = fetched.content_rows
    stats.fetched_comments = fetched.comment_rows

    # 2. 清洗
    cleaned = _apply_cleaning(fetched.contents)
    stats.no_meaningful_text = sum(
        1 for c in cleaned if not has_meaningful_text(c.analysis_text))

    # 3. 去重（清洗之后 —— 否则水军改标点的变体会漏掉）
    dedup = deduplicate(cleaned)
    stats.unique_count = len(dedup.unique_contents)
    stats.duplicate_count = dedup.duplicate_count

    # 4. 水军识别（基于全量指纹，包含被合并的内容）
    bot_signals = detect_bots(cleaned, dedup.fingerprints)
    stats.bot_accounts = len(bot_signals)

    result = IngestionResult(
        contents=dedup.unique_contents,
        duplicate_of=dedup.duplicate_of,
        fingerprints=dedup.fingerprints,
        bot_signals=bot_signals,
        per_source=fetched.per_source,
        stats=stats,
    )

    # 5. 多模态解析（去重之后 —— 重复内容不必重复调用外部服务）
    if resolve_media:
        resolution = await resolve_modality(
            result.contents,
            session=session if persist_modality else None,
        )
        result.modality = resolution
        result.contents = resolution.contents
        stats.modality_total = resolution.total
        stats.modality_succeeded = resolution.succeeded
        stats.modality_skipped = resolution.skipped

    return result


async def ingest(query: FetchQuery, *, resolve_media: bool = True,
                 persist_modality: bool = True,
                 session: AsyncSession | None = None) -> IngestionResult:
    """执行完整接入流程。

    `resolve_media=False` 可跳过 OCR/ASR——离线分析或外部服务不可用时的降级开关。
    """
    if session is not None:
        result = await _ingest(query, session, resolve_media=resolve_media,
                               persist_modality=persist_modality)
    else:
        async with session_scope() as own_session:
            result = await _ingest(query, own_session, resolve_media=resolve_media,
                                   persist_modality=persist_modality)

    logger.info("接入流程完成 | {}", result.stats.render().replace("\n", " | "))
    return result
