"""平台适配器基类。

**接入层的核心职责**：把四类异构原始表映射为统一的 `UnifiedContent`，
让上层（分析、检索、展示）再也不必感知平台差异。

用模板方法组织：基类负责共通流程（构造统一标识、组装字段、合并账号信息），
子类只实现真正不同的部分——文本取自哪个字段、互动量叫什么名字。

差异有多大？仅"评论数"一个概念，四张表就有三种写法：
`comments_count`（微博内容）、`comment_count`（抖音内容）、`reply_count`（论坛）。
这类差异必须**在这里收敛**，不能扩散到上层——否则每个分析模块都要写一遍 `if 平台 == ...`。
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, ClassVar

from engines.contracts.content import (
    RECORD_COMMENT,
    RECORD_POST,
    Author,
    Engagement,
    MediaRef,
    UnifiedContent,
    make_content_uid,
)
from engines.contracts.enums import SourceType


def to_int(value: Any) -> int:
    """把数据库里的互动量转成整数。

    原始表的互动量列是 `text` 类型（采集器的原始输出），值是形如 `'1613287'` 的字符串，
    也可能是空串、`None` 或带千分位的历史脏数据。任何一种都不该让接入流程崩掉。
    """
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().replace(',', '')
    if not text:
        return 0
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def to_str(value: Any) -> str:
    """空值统一成空字符串，避免上层到处判 None。"""
    if value is None:
        return ""
    return str(value).strip()


class SourceAdapter(ABC):
    """某类信源的适配器。

    子类必须声明：
      - source_type            信源类型
      - content_table          内容表名
      - comment_table          评论表名
      - content_business_field 内容表的业务主键字段（**不是**自增主键 id）
      - published_field        发布时间字段
      - parent_field           评论表中指向父内容的字段
    """

    source_type: ClassVar[SourceType]
    content_table: ClassVar[str]
    comment_table: ClassVar[str]
    content_business_field: ClassVar[str]
    published_field: ClassVar[str]
    parent_field: ClassVar[str]
    comment_business_field: ClassVar[str] = "comment_id"
    # 评论表的时间字段名。**不能复用 content 的 published_field**：
    # 新闻与论坛的内容表叫 publish_time，而它们的评论表叫 create_time，
    # 只有微博与抖音两表恰好同名——这是个"看起来能跑、换一类信源就炸"的陷阱。
    comment_published_field: ClassVar[str] = "create_time"

    # ------------------------------------------------------------------
    # 模板方法：对外入口
    # ------------------------------------------------------------------

    def to_content(self, row: Mapping[str, Any],
                   account: Mapping[str, Any] | None = None) -> UnifiedContent:
        """把内容表的一行映射为 UnifiedContent。

        `account` 是 platform_account 表里对应的账号行（可选）——原始内容表只有昵称，
        粉丝量与影响力在账号表里，由调用方查好后传入，保持适配器无 IO。
        """
        business_id = to_int(row.get(self.content_business_field))
        title, text = self._text_of(row)
        return UnifiedContent(
            content_uid=make_content_uid(self.source_type, self.content_table, business_id),
            source_type=self.source_type,
            source_table=self.content_table,
            business_id=business_id,
            record_type=RECORD_POST,
            author=self._merge_account(self._author_of(row), account),
            title=title,
            text=text,
            raw_text=text,
            published_at=to_int(row.get(self.published_field)),
            url=self._url_of(row),
            source_keyword=to_str(row.get("source_keyword")),
            engagement=self._engagement_of(row),
            media_refs=self._media_refs_of(row),
        )

    def to_comment(self, row: Mapping[str, Any],
                   account: Mapping[str, Any] | None = None) -> UnifiedContent:
        """把评论表的一行映射为 UnifiedContent。

        评论也是独立内容（有自己的 content_uid），同时带上父内容的标识——
        观点聚类需要按父内容把评论归并到事件。
        """
        business_id = to_int(row.get(self.comment_business_field))
        parent_business_id = to_int(row.get(self.parent_field))
        return UnifiedContent(
            content_uid=make_content_uid(self.source_type, self.comment_table, business_id),
            source_type=self.source_type,
            source_table=self.comment_table,
            business_id=business_id,
            record_type=RECORD_COMMENT,
            parent_content_uid=make_content_uid(
                self.source_type, self.content_table, parent_business_id),
            author=self._merge_account(self._author_of(row), account),
            text=to_str(row.get("content")),
            raw_text=to_str(row.get("content")),
            published_at=to_int(row.get(self.comment_published_field)),
            engagement=self._comment_engagement_of(row),
        )

    def parent_uid_of(self, parent_business_id: int) -> str:
        """由父内容的业务 ID 构造统一标识（供外部按 ID 反查时使用）。"""
        return make_content_uid(self.source_type, self.content_table, parent_business_id)

    # ------------------------------------------------------------------
    # 子类实现：文本与互动量
    # ------------------------------------------------------------------

    @abstractmethod
    def _text_of(self, row: Mapping[str, Any]) -> tuple[str, str]:
        """返回 (标题, 正文)。没有标题的平台返回空串。"""

    @abstractmethod
    def _engagement_of(self, row: Mapping[str, Any]) -> Engagement:
        """内容互动量。字段名各平台不同，在此收敛。"""

    @abstractmethod
    def _comment_engagement_of(self, row: Mapping[str, Any]) -> Engagement:
        """评论互动量。同样各平台不同（`comment_like_count` / `like_count`）。"""

    @abstractmethod
    def _url_of(self, row: Mapping[str, Any]) -> str:
        """原文链接。"""

    # ------------------------------------------------------------------
    # 子类可覆写：作者与时间
    # ------------------------------------------------------------------

    def _author_of(self, row: Mapping[str, Any]) -> Author:
        """从行内提取作者基本信息。默认取 user_id / nickname / ip_location 三件套。"""
        return Author(
            account_id=to_str(row.get("user_id")),
            nickname=to_str(row.get("nickname")),
            ip_location=to_str(row.get("ip_location")),
        )

    def _media_refs_of(self, row: Mapping[str, Any]) -> tuple[MediaRef, ...]:
        """内容携带的媒体资源。

        默认无——只有短视频平台的内容同时带封面图与视频，
        其余三类信源的原始表里没有内容级媒体地址。
        这些地址是多模态解析（OCR / ASR）的输入，映射时不能丢。
        """
        return ()

    def account_lookup_key(self, row: Mapping[str, Any]) -> str:
        """用于在 platform_account 表中查该行作者的键。

        默认是 user_id 三件套中的 ID 字段，但**新闻门户是例外**：
        它的原始表只存了媒体名与记者名，没有账号 ID，只能按媒体名匹配。
        调用方统一用本方法取键，不必为每个平台写分支。
        """
        return to_str(row.get("user_id"))

    @staticmethod
    def _merge_account(author: Author,
                       account: Mapping[str, Any] | None) -> Author:
        """把账号表里的权重信息合并进作者对象。

        账号表存在的原因：原始内容表**没有粉丝量字段**，
        而账号权重是风险分级与传播溯源的必要输入（PRD FR-29）。
        """
        if not account:
            return author
        return replace(
            author,
            follower_count=to_int(account.get("follower_count")),
            verified_type=to_str(account.get("verified_type")) or "普通用户",
            influence_score=float(account.get("influence_score") or 0.0),
            ip_location=author.ip_location or to_str(account.get("ip_location")),
        )
