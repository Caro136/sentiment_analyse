"""行业论坛适配器。

与前三类的差异：

1. **作者字段名不同**：用 `author_id` / `author_nickname`，而非 `user_id` / `nickname`。
2. **互动量只有浏览与回复**：论坛没有点赞机制，`view_count` 映射为 `likes`、
   `reply_count` 映射为 `replies`。
3. **回帖表叫 `forum_post`**：业务主键是 `post_id`，通过 `thread_id` 归属于主帖。

论坛讨论的专业性通常高于社交平台（长文分析、技术论证），
在证据调拨时值得与短文本区别对待。
"""

from collections.abc import Mapping
from typing import Any, ClassVar

from engines.contracts.content import Author, Engagement
from engines.contracts.enums import SourceType

from .base import SourceAdapter, to_int, to_str


class ForumAdapter(SourceAdapter):
    source_type: ClassVar[SourceType] = SourceType.FORUM
    content_table: ClassVar[str] = "forum_thread"
    comment_table: ClassVar[str] = "forum_post"
    content_business_field: ClassVar[str] = "thread_id"
    published_field: ClassVar[str] = "publish_time"
    parent_field: ClassVar[str] = "thread_id"
    comment_business_field: ClassVar[str] = "post_id"

    def _text_of(self, row: Mapping[str, Any]) -> tuple[str, str]:
        return to_str(row.get("title")), to_str(row.get("content"))

    def _engagement_of(self, row: Mapping[str, Any]) -> Engagement:
        return Engagement(
            likes=to_int(row.get("view_count")),      # 浏览量视作触达量
            replies=to_int(row.get("reply_count")),
        )

    def _comment_engagement_of(self, row: Mapping[str, Any]) -> Engagement:
        return Engagement(likes=to_int(row.get("like_count")))

    def _url_of(self, row: Mapping[str, Any]) -> str:
        return to_str(row.get("url"))

    def _author_of(self, row: Mapping[str, Any]) -> Author:
        # 论坛用 author_id / author_nickname，与社交媒体不同名
        return Author(
            account_id=to_str(row.get("author_id")),
            nickname=to_str(row.get("author_nickname")),
        )

    def account_lookup_key(self, row: Mapping[str, Any]) -> str:
        return to_str(row.get("author_id"))
