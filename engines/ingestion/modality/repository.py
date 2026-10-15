"""`modality_asset` 表的读写。

写入采用「先查后写」而非盲目 INSERT：同一资产可能被重复解析
（重跑接入流程、调整解析器后重试），`asset_id` 是幂等键——
重复写入应当更新结果而不是撞唯一索引报错。
"""

import time
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from engines.common.db.models import ModalityAsset
from engines.ingestion.modality.base import STATUS_FAILED, ModalityOutput


async def save_assets(session: AsyncSession,
                      outputs: Sequence[ModalityOutput]) -> int:
    """幂等写入解析产出。返回写入条数。"""
    if not outputs:
        return 0

    ids = [o.asset_id for o in outputs]
    existing_rows = await session.execute(
        select(ModalityAsset).where(ModalityAsset.asset_id.in_(ids)))
    existing = {row.asset_id: row for row in existing_rows.scalars()}

    now = int(time.time())
    written = 0
    for output in outputs:
        row = existing.get(output.asset_id)
        if row is None:
            row = ModalityAsset(asset_id=output.asset_id, created_at=now)
            session.add(row)
        row.content_uid = output.content_uid
        row.asset_type = output.asset_type
        row.origin_url = output.origin_url
        row.parsed_text = output.parsed_text
        row.parse_status = output.parse_status
        row.parser = output.parser
        row.error_message = output.error_message
        row.parsed_at = now
        written += 1

    await session.flush()
    return written


async def load_assets(session: AsyncSession,
                      content_uids: Sequence[str]) -> dict[str, list[ModalityOutput]]:
    """按内容标识取回解析结果，供详情页展示（FR-44 的 OCR / ASR 视图）。"""
    if not content_uids:
        return {}

    result = await session.execute(
        select(ModalityAsset).where(ModalityAsset.content_uid.in_(list(content_uids))))

    grouped: dict[str, list[ModalityOutput]] = {}
    for row in result.scalars():
        grouped.setdefault(row.content_uid, []).append(ModalityOutput(
            asset_id=row.asset_id,
            content_uid=row.content_uid,
            asset_type=row.asset_type,
            parser=row.parser or "",
            parse_status=row.parse_status or STATUS_FAILED,
            parsed_text=row.parsed_text or "",
            error_message=row.error_message or "",
            origin_url=row.origin_url or "",
        ))
    return grouped


def build_asset_id(content_uid: str, asset_key: str) -> str:
    """由内容标识与资产键构造稳定的资产 ID。

    稳定是关键：同一张图片重复解析应当更新同一条记录，
    而不是每次生成新 ID 导致表里堆积重复行。
    """
    return f"{content_uid}#{asset_key}"


__all__ = ["save_assets", "load_assets", "build_asset_id"]
