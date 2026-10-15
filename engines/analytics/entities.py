"""命名实体抽取与关系构建（PRD FR-25 / FR-26）。

实体 ID 由「类型 + 名称」哈希得到，**不是自增**：
同一个实体在多条内容中出现时必须是同一个 ID，否则图谱会散成一堆孤立节点。
用哈希还能让重复抽取（重跑分析）保持幂等——同一个实体永远得到同一个 ID。

关系用**共现**构建：同一条内容里出现的实体两两相连，权重为共现次数。
这是个简化（真实关系有「投诉」「回应」「属于」等语义），
但共现足以支撑「谁和谁总是一起被提到」这类图谱洞察，
且不需要为每对实体额外调用模型。
"""

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from engines.analytics.prompts import (
    ENTITY_EXTRACTION_SYSTEM,
    ENTITY_EXTRACTION_USER_TEMPLATE,
)
from engines.analytics.schemas import EntityExtraction
from engines.analytics.sentiment import default_client
from engines.common.logging import logger
from engines.common.runtime.concurrency import gather_limited
from engines.contracts.content import UnifiedContent

# 送入模型的文本上限：实体集中在开头与关键句，超长正文的边际收益很低
MAX_TEXT_LENGTH = 1200
DEFAULT_CONCURRENCY = 8

# 共现关系的类型标识
RELATION_COOCCURRENCE = "共现"


def make_entity_id(name: str, entity_type: str) -> str:
    """实体 ID：类型 + 名称的哈希。同名不同类型视为不同实体。"""
    digest = hashlib.md5(f"{entity_type}:{name}".encode("utf-8")).hexdigest()[:12]
    return f"ent_{digest}"


def make_relation_id(from_id: str, to_id: str, relation_type: str) -> str:
    """关系 ID：保证同一对实体同一关系只产生一条记录。"""
    left, right = sorted((from_id, to_id))
    digest = hashlib.md5(f"{left}|{right}|{relation_type}".encode("utf-8")).hexdigest()[:12]
    return f"rel_{digest}"


@dataclass(frozen=True)
class EntityNode:
    """图谱节点。"""

    entity_id: str
    name: str
    entity_type: str


@dataclass(frozen=True)
class EntityRelationEdge:
    """图谱边。"""

    relation_id: str
    from_entity_id: str
    to_entity_id: str
    relation_type: str = RELATION_COOCCURRENCE
    weight: float = 1.0


@dataclass
class ExtractionResult:
    """抽取产出。"""

    entities: list[EntityNode] = field(default_factory=list)
    relations: list[EntityRelationEdge] = field(default_factory=list)
    per_content: dict[str, list[str]] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)

    @property
    def entity_count(self) -> int:
        return len(self.entities)

    @property
    def relation_count(self) -> int:
        return len(self.relations)

    def top_entities(self, limit: int = 10) -> list[tuple[EntityNode, int]]:
        """按出现频次排序的实体，供图谱展示与洞察。"""
        counter: dict[str, int] = defaultdict(int)
        for ids in self.per_content.values():
            for entity_id in ids:
                counter[entity_id] += 1
        by_id = {e.entity_id: e for e in self.entities}
        ranked = sorted(counter.items(), key=lambda kv: -kv[1])[:limit]
        return [(by_id[eid], count) for eid, count in ranked if eid in by_id]

    def types(self) -> dict[str, int]:
        """各实体类型的数量分布。"""
        counter: dict[str, int] = defaultdict(int)
        for entity in self.entities:
            counter[entity.entity_type] += 1
        return dict(counter)


async def _extract_one(content: UnifiedContent, client) -> EntityExtraction:
    prompt = ENTITY_EXTRACTION_USER_TEMPLATE.format(
        text=content.analysis_text[:MAX_TEXT_LENGTH])
    return await client.generate_object(prompt, EntityExtraction,
                                        system=ENTITY_EXTRACTION_SYSTEM)


async def extract_entities(
    contents: Sequence[UnifiedContent],
    *,
    client=None,
    max_concurrency: int = DEFAULT_CONCURRENCY,
) -> ExtractionResult:
    """批量抽取实体并构建共现关系。单条失败不影响其余。"""
    if not contents:
        return ExtractionResult()

    llm = client or default_client()
    outcomes = await gather_limited(
        [_extract_one(content, llm) for content in contents],
        limit=max_concurrency)

    entities: dict[str, EntityNode] = {}
    per_content: dict[str, list[str]] = {}
    cooccurrence: dict[tuple[str, str], int] = defaultdict(int)
    failed: list[str] = []

    for content, outcome in zip(contents, outcomes):
        if isinstance(outcome, BaseException):
            failed.append(content.content_uid)
            logger.warning("实体抽取失败 uid={} err={}",
                           content.content_uid, type(outcome).__name__)
            continue

        ids: list[str] = []
        for item in outcome.entities:
            name = (item.name or "").strip()
            if not name:
                continue
            entity_id = make_entity_id(name, item.entity_type)
            entities[entity_id] = EntityNode(entity_id, name, item.entity_type)
            ids.append(entity_id)

        # 同一条内容里重复提到同一实体只算一次
        unique_ids = list(dict.fromkeys(ids))
        per_content[content.content_uid] = unique_ids

        for i in range(len(unique_ids)):
            for j in range(i + 1, len(unique_ids)):
                pair = (min(unique_ids[i], unique_ids[j]),
                        max(unique_ids[i], unique_ids[j]))
                cooccurrence[pair] += 1

    relations = [
        EntityRelationEdge(
            relation_id=make_relation_id(a, b, RELATION_COOCCURRENCE),
            from_entity_id=a, to_entity_id=b, weight=float(weight))
        for (a, b), weight in cooccurrence.items()
    ]

    logger.info("实体抽取完成：{} 条内容 → {} 个实体 / {} 条关系（失败 {}）",
                len(contents) - len(failed), len(entities), len(relations), len(failed))

    return ExtractionResult(entities=list(entities.values()),
                            relations=relations,
                            per_content=per_content,
                            failed=failed)
