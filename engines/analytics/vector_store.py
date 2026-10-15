"""证据向量库（Milvus）。

**为什么需要它**：关键词召回只能命中字面相同的表述。同一条抱怨有无数种说法
（「发热严重」「后盖烫手」「可以当暖手宝」），主题词是「星耀XPhone15」，
纯关键词匹配会把大部分真实讨论漏掉。语义召回补的正是这块。

设计取舍：

1. **只存稠密向量**。参考实现用了 BGE-M3 的稠密 + 稀疏双向量，
   但那是本地模型才有的能力；云端 embedding 只给稠密向量。
   "混合检索"因此改为**向量召回 + 关键词召回在应用层用 RRF 融合**——
   这不是退而求其次，RRF 本来就更适合融合异构检索源（不同来源的分数量纲不可比）。
2. **标量字段用动态字段**。`content` / `source_type` 等随记录走，
   不必在 schema 里逐个声明；代价是过滤表达能力略弱，
   但过滤条件（时间窗、信源）在 MySQL 侧做更合适。
3. **主键用 content_uid**，与全链路统一标识一致，天然幂等。
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from engines.common.logging import logger

# 主键最大长度
MAX_UID_LENGTH = 256
DEFAULT_TIMEOUT = 10.0


@dataclass(frozen=True)
class VectorRecord:
    """一条待入库的向量记录。"""

    content_uid: str
    vector: list[float]
    content: str = ""
    source_type: str = ""
    source_table: str = ""
    published_at: int = 0
    hotness_score: float = 0.0
    source_keyword: str = ""


@dataclass(frozen=True)
class SearchHit:
    """一条检索结果。"""

    content_uid: str
    score: float
    content: str = ""
    source_type: str = ""
    published_at: int = 0
    hotness_score: float = 0.0

    @property
    def similarity(self) -> float:
        """余弦相似度语义下的分数（Milvus 的 COSINE 返回的即是相似度）。"""
        return self.score


@dataclass
class CollectionStats:
    """集合状态。"""

    name: str = ""
    exists: bool = False
    row_count: int = 0
    dimension: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def _require_milvus():
    try:
        from pymilvus import MilvusClient
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 Milvus 依赖，请执行：uv sync --extra vector") from exc
    return MilvusClient


class EvidenceVectorStore:
    """证据向量库的读写。"""

    def __init__(self, uri: str, collection_name: str,
                 db_name: str = "default", timeout: float = DEFAULT_TIMEOUT) -> None:
        self._uri = uri
        self._collection = collection_name
        self._db_name = db_name
        self._timeout = timeout
        self._client: Any = None

    @classmethod
    def from_settings(cls) -> "EvidenceVectorStore":
        from engines.contracts.config import get_settings

        settings = get_settings()
        return cls(uri=settings.milvus_uri,
                   collection_name=settings.milvus_insight_collection,
                   db_name=settings.milvus_db_name)

    @property
    def collection_name(self) -> str:
        return self._collection

    def _get_client(self):
        if self._client is None:
            MilvusClient = _require_milvus()
            self._client = MilvusClient(uri=self._uri, db_name=self._db_name,
                                        timeout=self._timeout)
        return self._client

    # ------------------------------------------------------------------
    # 集合管理
    # ------------------------------------------------------------------

    def ensure_collection(self, dimension: int) -> bool:
        """确保集合存在。返回是否新建。

        维度必须与编码模型一致——换了模型（如从 1024 维换到 768 维）
        必须重建集合，否则写入会因维度不匹配失败。
        """
        client = self._get_client()
        if client.has_collection(self._collection):
            return False

        client.create_collection(
            collection_name=self._collection,
            dimension=dimension,
            primary_field_name="content_uid",
            id_type="string",
            vector_field_name="vector",
            metric_type="COSINE",
            auto_id=False,
            max_length=MAX_UID_LENGTH,
            enable_dynamic_field=True,   # content / source_type 等随记录携带
        )
        logger.info("已创建 Milvus 集合 {}（维度 {}）", self._collection, dimension)
        return True

    def drop_collection(self) -> None:
        """删除集合（模型维度变更后重建时使用）。"""
        client = self._get_client()
        if client.has_collection(self._collection):
            client.drop_collection(self._collection)
            logger.warning("已删除 Milvus 集合 {}", self._collection)

    def stats(self) -> CollectionStats:
        client = self._get_client()
        if not client.has_collection(self._collection):
            return CollectionStats(name=self._collection, exists=False)
        info = client.describe_collection(self._collection)
        result = CollectionStats(name=self._collection, exists=True)
        try:
            # get_collection_stats 的行数不实时（与 flush 时机有关），
            # 用 count(*) 查询才是写入后立刻可见的准确值
            rows = client.query(collection_name=self._collection,
                                filter="", output_fields=["count(*)"])
            result.row_count = int(rows[0].get("count(*)", 0)) if rows else 0
        except Exception:  # noqa: BLE001  统计失败不影响主流程
            pass
        for f in info.get("fields", []):
            if f.get("name") == "vector":
                result.dimension = int(f.get("params", {}).get("dim", 0) or 0)
        return result

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def upsert(self, records: Sequence[VectorRecord], *, batch_size: int = 200) -> int:
        """幂等写入。主键是 content_uid，重复写入即更新。"""
        if not records:
            return 0
        client = self._get_client()

        written = 0
        for start in range(0, len(records), batch_size):
            batch = records[start:start + batch_size]
            payload = [{
                "content_uid": r.content_uid,
                "vector": r.vector,
                "content": r.content[:2000],
                "source_type": r.source_type,
                "source_table": r.source_table,
                "published_at": int(r.published_at),
                "hotness_score": float(r.hotness_score),
                "source_keyword": r.source_keyword,
            } for r in batch]
            client.upsert(collection_name=self._collection, data=payload)
            written += len(batch)

        logger.info("向量库写入 {} 条", written)
        return written

    def delete(self, content_uids: Sequence[str]) -> int:
        if not content_uids:
            return 0
        client = self._get_client()
        client.delete(collection_name=self._collection, ids=list(content_uids))
        return len(content_uids)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def search(self, vector: list[float], *, top_k: int = 20,
               expr: str | None = None) -> list[SearchHit]:
        """向量检索。`expr` 是 Milvus 的过滤表达式，如 `published_at >= 123`。"""
        client = self._get_client()
        results = client.search(
            collection_name=self._collection,
            data=[vector],
            limit=top_k,
            output_fields=["content", "source_type", "published_at", "hotness_score"],
            filter=expr or "",
        )

        hits: list[SearchHit] = []
        for row in (results[0] if results else []):
            entity = row.get("entity") or {}
            # 主键字段名是自定义的 content_uid，返回结构里就叫这个名字而不是 "id"；
            # 两种都取，避免因为 Milvus 客户端版本差异而静默拿到空标识。
            uid = (row.get("content_uid") or row.get("id")
                   or entity.get("content_uid") or "")
            hits.append(SearchHit(
                content_uid=str(uid),
                score=float(row.get("distance", 0.0)),
                content=str(entity.get("content", "")),
                source_type=str(entity.get("source_type", "")),
                published_at=int(entity.get("published_at", 0) or 0),
                hotness_score=float(entity.get("hotness_score", 0.0) or 0.0),
            ))
        return hits

    def __repr__(self) -> str:
        return f"<EvidenceVectorStore uri={self._uri} collection={self._collection}>"
