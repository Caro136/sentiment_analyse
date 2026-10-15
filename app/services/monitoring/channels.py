"""信源通道用例（FR-11~FR-13）。

**通道不由用户增删**：四类信源是系统定义的（`SourceType` 的四个取值，与原始表一一对应），
通道集合因此也是固定的——使用者能做的是启停与调参。所以没有「新建通道」接口，
缺失的通道在首次访问时按默认值**幂等补齐**（`ensure_default_channels`），
省掉一步「先跑种子脚本」的安装步骤，也避免页面上出现三种通道的怪状态。
"""

import time

from loguru import logger

from app.errors import not_found
from app.services.audit import audit, note_change
from app.services.monitoring.repository import (
    ChannelRecord,
    MonitoringRepository,
    new_channel_id,
)
from engines.contracts.enums import AuditAction, SourceType
from engines.ingestion.reader import SourceProbe, probe_source

__all__ = [
    "DEFAULT_CHANNEL_SETTINGS",
    "ensure_default_channels",
    "list_channels",
    "update_channel",
    "check_channels",
]

# 通道默认参数：(显示名, 轮询间隔分钟, 回溯窗口小时)
# 15 分钟 / 24 小时与设计规格 8.2 的性能目标一致（预警 ≤3min 靠事件驱动，不靠更密的轮询）
DEFAULT_CHANNEL_SETTINGS: dict[SourceType, tuple[str, int, int]] = {
    SourceType.SOCIAL: ("社交媒体", 15, 24),
    SourceType.VIDEO: ("短视频", 15, 24),
    SourceType.NEWS: ("新闻门户", 30, 72),
    SourceType.FORUM: ("行业论坛", 60, 168),
}


async def ensure_default_channels(repo: MonitoringRepository) -> list[ChannelRecord]:
    """补齐缺失的通道并返回全部通道（幂等）。

    新闻门户与行业论坛的发文频率天然低于社交平台，默认轮询间隔与回溯窗口因此更宽
    ——参数不同是业务事实，不是随手填的。
    """
    existing = {channel.channel_id: channel for channel in await repo.list_channels()}
    now = int(time.time())

    created: list[ChannelRecord] = []
    for source_type, (name, interval, lookback) in DEFAULT_CHANNEL_SETTINGS.items():
        channel_id = new_channel_id(source_type.value)
        if channel_id in existing:
            continue
        record = ChannelRecord(
            channel_id=channel_id,
            channel_type=source_type.value,
            name=name,
            enabled=True,
            poll_interval_min=interval,
            lookback_hours=lookback,
            updated_at=now,
        )
        created.append(await repo.create_channel(record))

    if created:
        logger.info("补齐信源通道：{}", ", ".join(c.channel_id for c in created))

    channels = await repo.list_channels()
    channels.sort(key=lambda c: _order_of(c.channel_type))
    return channels


def _order_of(channel_type: str) -> int:
    """按 SourceType 的声明顺序返回固定次序（页面上的通道顺序不该随 id 漂移）。"""
    for index, source_type in enumerate(SourceType):
        if source_type.value == channel_type:
            return index
    return len(SourceType)


async def list_channels(repo: MonitoringRepository) -> list[ChannelRecord]:
    return await ensure_default_channels(repo)


async def _require_channel(repo: MonitoringRepository, channel_id: str) -> ChannelRecord:
    await ensure_default_channels(repo)
    channel = await repo.get_channel(channel_id)
    if channel is None:
        raise not_found(f"信源通道不存在：{channel_id}", detail=f"channel_id={channel_id}")
    return channel


def _snapshot(channel: ChannelRecord) -> dict:
    return {
        "enabled": channel.enabled,
        "poll_interval_min": channel.poll_interval_min,
        "lookback_hours": channel.lookback_hours,
    }


@audit(AuditAction.CONFIG_UPDATE, target="channel:{channel_id}")
async def update_channel(repo: MonitoringRepository, *, channel_id: str,
                         enabled: bool | None = None, poll_interval_min: int | None = None,
                         lookback_hours: int | None = None) -> ChannelRecord:
    """启停通道 / 调整采集频率与回溯窗口（FR-11/12）。

    停用只影响后续采集，**已入库的历史数据照常可查**（FR-11 的验收要求）：
    通道状态只写在 `source_channel`，不回溯删除任何内容。
    """
    channel = await _require_channel(repo, channel_id)

    fields: dict = {}
    if enabled is not None:
        fields["enabled"] = bool(enabled)
    if poll_interval_min is not None:
        fields["poll_interval_min"] = int(poll_interval_min)
    if lookback_hours is not None:
        fields["lookback_hours"] = int(lookback_hours)

    if not fields:
        return channel

    fields["updated_at"] = int(time.time())
    updated = await repo.update_channel(channel_id, fields)
    if updated is None:
        raise not_found(f"信源通道不存在：{channel_id}")

    note_change(before=_snapshot(channel), after=_snapshot(updated))
    logger.info("更新通道 channel_id={} 字段={}", channel_id, sorted(fields))
    return updated


async def check_channels(repo: MonitoringRepository, session, *,
                         channel_id: str | None = None) -> list[dict]:
    """通道连通性自检（FR-13）。

    返回的是**可核对的事实**，而不是一句「连通正常」：库是否可达、通道对应的原始表
    有没有数据、最新一条的时间戳、以及最近一次采集记录。本项目没有真实采集器，
    因此不去探测外部站点——那会给出一个查不到的「通道正常」。
    """
    channels = await list_channels(repo)
    if channel_id:
        channels = [await _require_channel(repo, channel_id)]

    results: list[dict] = []
    for channel in channels:
        source_type = SourceType(channel.channel_type)
        payload = {
            "channel_id": channel.channel_id,
            "channel_type": source_type.value,
            "type_label": source_type.label,
            "name": channel.name,
            "enabled": channel.enabled,
            "last_collected_at": channel.last_collected_at,
            "last_collected_count": channel.last_collected_count,
        }

        try:
            probe: SourceProbe = await probe_source(source_type, session)
        except Exception as exc:  # noqa: BLE001 —— 自检本身不能把接口打挂
            logger.warning("通道自检失败 channel_id={}：{}", channel.channel_id,
                           type(exc).__name__)
            payload.update({
                "db_reachable": False,
                "message": f"原始表查询失败：{type(exc).__name__}",
            })
            results.append(payload)
            continue

        payload.update({
            "db_reachable": True,
            "content_table": probe.content_table,
            "comment_table": probe.comment_table,
            "content_rows": probe.content_rows,
            "comment_rows": probe.comment_rows,
            "latest_published_at": probe.latest_published_at,
            "message": _check_message(channel, probe),
        })
        results.append(payload)

    return results


def _check_message(channel: ChannelRecord, probe: SourceProbe) -> str:
    if not channel.enabled:
        return "通道已停用（自检仍可执行，历史数据可查）"
    if not probe.has_data:
        return "原始表无数据——通道可用但当前没有内容"
    published = probe.latest_published_at
    if published is None:
        return "原始表有数据但缺少发布时间，请核对采集器"
    return f"原始表 {probe.content_rows} 条内容 / {probe.comment_rows} 条评论"
