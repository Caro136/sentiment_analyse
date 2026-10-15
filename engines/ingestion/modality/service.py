"""多模态解析服务。

把「内容携带的媒体资源」批量解析为文本，落库，并回填到 `UnifiedContent.modality_texts`。

设计要点：

- **并行但限流**。不同资产的解析互不依赖，可以并发；但外部服务会限流，
  并发数必须可控（默认 5）。
- **单条失败不影响其他**。某个图片识别失败，其余照常解析——
  被一条坏链接拖垮整批是最典型的自伤。
- **解析结果回填为不可变对象**。`UnifiedContent` 是 frozen dataclass，
  回填时构造新实例而不是原地修改，保证已发出的引用不会被悄悄改变。
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncSession

from engines.common.logging import logger
from engines.common.runtime.concurrency import DEFAULT_LIMIT, gather_limited
from engines.contracts.content import UnifiedContent
from engines.ingestion.modality.base import (
    STATUS_FAILED,
    ModalityInput,
    ModalityOutput,
)
from engines.ingestion.modality.registry import get_parser
from engines.ingestion.modality.repository import build_asset_id, save_assets


@dataclass
class ModalityResolution:
    """一次批量解析的结果。"""

    contents: list[UnifiedContent]
    outputs: list[ModalityOutput]
    skipped: int = 0          # 无媒体资源、无需解析的内容条数

    @property
    def total(self) -> int:
        return len(self.outputs)

    @property
    def succeeded(self) -> int:
        return sum(1 for o in self.outputs if o.succeeded)

    @property
    def failed(self) -> int:
        return self.total - self.succeeded

    @property
    def summary(self) -> str:
        return (f"解析 {self.total} 个资产（成功 {self.succeeded} / 失败 {self.failed}），"
                f"{self.skipped} 条内容无媒体资源")


def collect_inputs(contents: Sequence[UnifiedContent]) -> list[ModalityInput]:
    """把内容的媒体引用展开为待解析项。"""
    items: list[ModalityInput] = []
    for content in contents:
        for ref in content.media_refs:
            items.append(ModalityInput(
                asset_id=build_asset_id(content.content_uid, ref.asset_key),
                content_uid=content.content_uid,
                asset_type=ref.asset_type,
                origin_url=ref.url,
            ))
    return items


async def _parse_one(item: ModalityInput) -> ModalityOutput:
    """解析单个资产。任何异常都转成 failed 产出，不向上抛（ADR-06）。"""
    try:
        parser = get_parser(item.asset_type)
    except KeyError as exc:
        return ModalityOutput(
            asset_id=item.asset_id, content_uid=item.content_uid,
            asset_type=item.asset_type, parser="none",
            parse_status=STATUS_FAILED, error_message=str(exc),
            origin_url=item.origin_url)

    try:
        return await parser.parse(item)
    except Exception as exc:  # noqa: BLE001  解析器理论上不该抛，这里是最后一道兜底
        from engines.common.logging import mask_secret

        logger.error("解析器异常 asset={} err={}", item.asset_id,
                     mask_secret(f"{type(exc).__name__}: {exc}")[:200])
        return ModalityOutput(
            asset_id=item.asset_id, content_uid=item.content_uid,
            asset_type=item.asset_type, parser=getattr(parser, "parser_name", "unknown"),
            parse_status=STATUS_FAILED, error_message=str(exc),
            origin_url=item.origin_url)


def backfill_texts(contents: Sequence[UnifiedContent],
                   outputs: Sequence[ModalityOutput]) -> list[UnifiedContent]:
    """把成功的解析文本回填到内容的 modality_texts。"""
    texts_by_content: dict[str, list[str]] = defaultdict(list)
    for output in outputs:
        if output.succeeded and output.has_text:
            texts_by_content[output.content_uid].append(output.parsed_text)

    if not texts_by_content:
        return list(contents)

    return [
        replace(content,
                modality_texts=tuple(texts_by_content[content.content_uid]))
        if content.content_uid in texts_by_content else content
        for content in contents
    ]


async def resolve_modality(
    contents: Sequence[UnifiedContent],
    *,
    session: AsyncSession | None = None,
    max_concurrency: int = DEFAULT_LIMIT,
) -> ModalityResolution:
    """批量解析多模态内容。

    `session` 不为 None 时同时落库。解析失败不阻断——返回的内容照常可用，
    只是少了附加文本；失败明细在 `outputs` 里，状态为 failed。
    """
    skipped = sum(1 for c in contents if not c.media_refs)
    items = collect_inputs(contents)

    if not items:
        return ModalityResolution(contents=list(contents), outputs=[], skipped=skipped)

    results = await gather_limited([_parse_one(i) for i in items],
                                   limit=max_concurrency)

    outputs: list[ModalityOutput] = []
    for index, result in enumerate(results):
        if isinstance(result, BaseException):
            item = items[index]
            outputs.append(ModalityOutput(
                asset_id=item.asset_id, content_uid=item.content_uid,
                asset_type=item.asset_type, parser="unknown",
                parse_status=STATUS_FAILED, error_message=str(result)[:500],
                origin_url=item.origin_url))
        else:
            outputs.append(result)

    if session is not None:
        written = await save_assets(session, outputs)
        logger.info("多模态解析结果已落库 {} 条", written)

    resolution = ModalityResolution(
        contents=backfill_texts(contents, outputs),
        outputs=outputs,
        skipped=skipped,
    )
    logger.info("多模态解析完成 | {}", resolution.summary)
    return resolution
