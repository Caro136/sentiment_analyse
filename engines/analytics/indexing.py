"""内容向量索引：把内容编码后写入 Milvus。

索引的是**去重后的内容**（接入流程已经合并过同质内容）——
重复内容入库只会浪费向量存储，检索时还会挤占结果位。

维度一致性是这里最容易出问题的地方：换了编码模型（比如从 1024 维的
text-embedding-v4 换到别的），集合维度不会自动跟着变，
写入时会报维度不匹配。这里在写入前显式校验并给出可执行的提示。
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass

from engines.analytics.embedding import Embedder
from engines.analytics.vector_store import EvidenceVectorStore, VectorRecord
from engines.common.logging import logger
from engines.contracts.content import UnifiedContent

# 送入编码的文本长度上限：超出部分对语义检索的区分度贡献很低
MAX_INDEX_TEXT_LENGTH = 600


@dataclass
class IndexResult:
    """索引结果。"""

    requested: int = 0
    indexed: int = 0
    dimension: int = 0
    duration: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def render(self) -> str:
        if self.error:
            return f"索引失败：{self.error}"
        return (f"索引 {self.indexed}/{self.requested} 条，"
                f"维度 {self.dimension}，耗时 {self.duration:.1f}s")


def _index_text(content: UnifiedContent) -> str:
    """用于编码的文本。

    正文 + 标题拼接：标题常包含事件主体的关键信息，只编码正文会丢掉这部分信号。
    """
    parts = [content.display_text]
    if content.title and content.title not in content.display_text:
        parts.insert(0, content.title)
    text = " ".join(p for p in parts if p).strip()
    return text[:MAX_INDEX_TEXT_LENGTH]


async def index_contents(
    contents: Sequence[UnifiedContent],
    *,
    store: EvidenceVectorStore | None = None,
    embedder: Embedder | None = None,
    recreate_on_mismatch: bool = False,
) -> IndexResult:
    """把内容编码并写入向量库。

    单条内容为空文本时跳过（编码空串没有意义，还会污染检索结果）。
    """
    started = time.monotonic()
    usable = [(c, _index_text(c)) for c in contents if _index_text(c).strip()]
    result = IndexResult(requested=len(contents))

    if not usable:
        result.error = "没有可索引的内容（文本均为空）"
        return result

    encoder = embedder or Embedder.from_settings()
    vector_store = store or EvidenceVectorStore.from_settings()

    try:
        vectors = await encoder.embed([text for _, text in usable])
    except Exception as exc:  # noqa: BLE001
        result.error = f"编码失败：{type(exc).__name__}"
        logger.warning("向量索引失败（编码）：{}", result.error)
        return result

    dimension = len(vectors[0]) if vectors else 0
    if dimension == 0:
        result.error = "编码返回空向量"
        return result
    result.dimension = dimension

    # ---- 维度校验：换模型后集合维度不会自动变 ----
    vector_store.ensure_collection(dimension)
    stats = vector_store.stats()
    if stats.dimension and stats.dimension != dimension:
        if not recreate_on_mismatch:
            result.error = (
                f"集合维度 {stats.dimension} 与当前编码维度 {dimension} 不一致。"
                f"换过编码模型的话需要重建集合："
                f"EvidenceVectorStore(...).drop_collection() 后重新索引")
            logger.error(result.error)
            return result
        logger.warning("集合维度 {} 与编码维度 {} 不一致，重建集合",
                       stats.dimension, dimension)
        vector_store.drop_collection()
        vector_store.ensure_collection(dimension)

    records = [
        VectorRecord(
            content_uid=content.content_uid,
            vector=vector,
            content=text,
            source_type=content.source_type.value,
            source_table=content.source_table,
            published_at=content.published_at,
            hotness_score=float(content.engagement.total),
            source_keyword=content.source_keyword,
        )
        for (content, text), vector in zip(usable, vectors)
    ]

    try:
        result.indexed = vector_store.upsert(records)
    except Exception as exc:  # noqa: BLE001
        result.error = f"写入失败：{type(exc).__name__}: {str(exc)[:120]}"
        logger.warning("向量索引失败（写入）：{}", result.error)
        return result

    result.duration = time.monotonic() - started
    logger.info(result.render())
    return result
