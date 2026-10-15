"""社交媒体（微博）适配器。

字段差异要点（与短视频对比着看更清楚）：
  - 正文叫 `content`，没有独立标题字段
  - 评论数叫 `comments_count`（抖音叫 `comment_count`）
  - 分享数叫 `shared_count`（抖音叫 `share_count`）
  - 评论点赞叫 `comment_like_count`（抖音叫 `like_count`）
"""

from collections.abc import Mapping
from typing import Any, ClassVar

from engines.contracts.content import Author, Engagement
from engines.contracts.enums import SourceType

from .base import SourceAdapter, to_int, to_str


class WeiboAdapter(SourceAdapter):
    source_type: ClassVar[SourceType] = SourceType.SOCIAL
    content_table: ClassVar[str] = "weibo_note"
    comment_table: ClassVar[str] = "weibo_note_comment"
    content_business_field: ClassVar[str] = "note_id"
    published_field: ClassVar[str] = "create_time"
    parent_field: ClassVar[str] = "note_id"
    comment_business_field: ClassVar[str] = "comment_id"

    def _text_of(self, row: Mapping[str, Any]) -> tuple[str, str]:
        # 微博没有独立标题，正文即全部内容
        return "", to_str(row.get("content"))

    def _engagement_of(self, row: Mapping[str, Any]) -> Engagement:
        return Engagement(
            likes=to_int(row.get("liked_count")),
            comments=to_int(row.get("comments_count")),
            shares=to_int(row.get("shared_count")),
        )

    def _comment_engagement_of(self, row: Mapping[str, Any]) -> Engagement:
        return Engagement(likes=to_int(row.get("comment_like_count")))

    def _url_of(self, row: Mapping[str, Any]) -> str:
        return to_str(row.get("note_url"))

    def _author_of(self, row: Mapping[str, Any]) -> Author:
        return Author(
            account_id=to_str(row.get("user_id")),
            nickname=to_str(row.get("nickname")),
            ip_location=to_str(row.get("ip_location")),
        )
