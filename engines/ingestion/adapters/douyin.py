"""短视频（抖音）适配器。

字段差异要点：
  - 同时有 `title` 与 `desc`，`desc` 是完整描述（微博只有 `content`）
  - 互动量比微博多一个 `collected_count`（收藏）
  - 评论数叫 `comment_count`、分享数叫 `share_count`、评论点赞叫 `like_count`
    ——与微博**逐字段都不同**，这正是接入层必须存在的原因
"""

from collections.abc import Mapping
from typing import Any, ClassVar

from engines.contracts.content import ASSET_IMAGE, ASSET_VIDEO, Author, Engagement, MediaRef
from engines.contracts.enums import SourceType

from .base import SourceAdapter, to_int, to_str


class DouyinAdapter(SourceAdapter):
    source_type: ClassVar[SourceType] = SourceType.VIDEO
    content_table: ClassVar[str] = "douyin_aweme"
    comment_table: ClassVar[str] = "douyin_aweme_comment"
    content_business_field: ClassVar[str] = "aweme_id"
    published_field: ClassVar[str] = "create_time"
    parent_field: ClassVar[str] = "aweme_id"
    comment_business_field: ClassVar[str] = "comment_id"

    def _text_of(self, row: Mapping[str, Any]) -> tuple[str, str]:
        return to_str(row.get("title")), to_str(row.get("desc"))

    def _engagement_of(self, row: Mapping[str, Any]) -> Engagement:
        return Engagement(
            likes=to_int(row.get("liked_count")),
            comments=to_int(row.get("comment_count")),
            shares=to_int(row.get("share_count")),
            collects=to_int(row.get("collected_count")),
        )

    def _comment_engagement_of(self, row: Mapping[str, Any]) -> Engagement:
        return Engagement(likes=to_int(row.get("like_count")))

    def _url_of(self, row: Mapping[str, Any]) -> str:
        return to_str(row.get("aweme_url"))

    def _author_of(self, row: Mapping[str, Any]) -> Author:
        return Author(
            account_id=to_str(row.get("user_id")),
            nickname=to_str(row.get("nickname")),
            ip_location=to_str(row.get("ip_location")),
        )

    def _media_refs_of(self, row: Mapping[str, Any]) -> tuple[MediaRef, ...]:
        """抖音内容同时带封面图与视频：封面走 OCR，视频走 ASR。

        这是四类信源中唯一有内容级媒体地址的——其余三类的原始表里没有，
        所以真实的多模态解析链路由抖音内容驱动。
        """
        refs: list[MediaRef] = []
        cover = to_str(row.get("cover_url"))
        if cover:
            refs.append(MediaRef(asset_type=ASSET_IMAGE, url=cover, suffix="cover"))
        video = to_str(row.get("video_download_url"))
        if video:
            refs.append(MediaRef(asset_type=ASSET_VIDEO, url=video, suffix="video"))
        return tuple(refs)
