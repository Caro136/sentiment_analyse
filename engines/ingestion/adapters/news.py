"""新闻门户适配器。

与社交/短视频的两个关键差异：

1. **有独立标题**。新闻报道的标题本身就是信息载体，不能只取正文。
2. **作者是机构而非个人**。原始表里是 `media_name`（媒体名）+ `author`（记者署名），
   没有账号 ID —— 所以查账号权重时只能按媒体名匹配（见 `account_lookup_key`）。

互动量的语义也不同：新闻没有"点赞"，`read_count` 是阅读量。把它映射到 `likes`
参与热度计算，是因为在这个场景下"触达人数"才是传播力的体现。
"""

from collections.abc import Mapping
from typing import Any, ClassVar

from engines.contracts.content import Author, Engagement
from engines.contracts.enums import SourceType

from .base import SourceAdapter, to_int, to_str


class NewsAdapter(SourceAdapter):
    source_type: ClassVar[SourceType] = SourceType.NEWS
    content_table: ClassVar[str] = "news_article"
    comment_table: ClassVar[str] = "news_article_comment"
    # 新闻表没有单独的业务 ID 字段，直接用自增主键
    content_business_field: ClassVar[str] = "id"
    published_field: ClassVar[str] = "publish_time"
    parent_field: ClassVar[str] = "article_id"
    comment_business_field: ClassVar[str] = "comment_id"

    search_fields = ("title", "content")

    def _text_of(self, row: Mapping[str, Any]) -> tuple[str, str]:
        return to_str(row.get("title")), to_str(row.get("content"))

    def _engagement_of(self, row: Mapping[str, Any]) -> Engagement:
        return Engagement(
            likes=to_int(row.get("read_count")),      # 阅读量视作触达量
            comments=to_int(row.get("comment_count")),
            shares=to_int(row.get("share_count")),
        )

    def _comment_engagement_of(self, row: Mapping[str, Any]) -> Engagement:
        return Engagement(likes=to_int(row.get("comment_like_count")))

    def _url_of(self, row: Mapping[str, Any]) -> str:
        return to_str(row.get("url"))

    def _author_of(self, row: Mapping[str, Any]) -> Author:
        media = to_str(row.get("media_name"))
        reporter = to_str(row.get("author"))
        return Author(
            account_id="",                       # 新闻表无账号 ID
            nickname=media or reporter,
            verified_type="媒体",
        )

    def account_lookup_key(self, row: Mapping[str, Any]) -> str:
        """新闻按媒体名匹配账号表——原始表里没有其他可用于关联的标识。"""
        return to_str(row.get("media_name"))

    def _comment_author_of(self, row: Mapping[str, Any]) -> Author:
        """新闻评论的作者是**读者**：按个人账号三件套取（`nickname` / `user_id`）。

        不能沿用内容行的 `media_name` 规则——评论行里没有这个字段，
        结果是昵称全空、认证被标成「媒体」。
        """
        return self._person_author(row)

    def comment_account_lookup_key(self, row: Mapping[str, Any]) -> str:
        """**新闻评论的作者是读者**，有 `user_id`，按它匹配账号表。

        不能沿用内容行的媒体名匹配：评论区所有人会被当成同一个媒体，
        粉丝量与影响力也就全错了（新闻评论行里根本没有 media_name）。
        """
        return to_str(row.get("user_id"))
