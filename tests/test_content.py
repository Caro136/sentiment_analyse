"""内容查询用例测试（FR-42~FR-44）。

重点在四个「看起来对、其实不对」的地方：

1. **排序**：统一按发布时间倒序（不是打标时间），分页由应用层完成；
2. **分页与总数**：筛选后的总数与页内条数要自洽（详情缺原文时除外）；
3. **截断**：列表 200 字且带 `text_truncated` 标记，详情必须是全文；
4. **降级**：标签存在但原文缺失时跳过该条而不是整体报错；
   `risk_factors` 是坏 JSON 时也要能出详情。
"""

import asyncio
import unittest

from app.errors import AppError
from app.services.content import service
from tests.fakes import (
    InMemoryContentRepository,
    make_analysis,
    make_content,
    make_raw_hit,
    patch_raw_layer,
)

WEIBO_1 = "social_media:weibo_note:1"
WEIBO_2 = "social_media:weibo_note:2"
DOUYIN_1 = "short_video:douyin_aweme:11"
NEWS_1 = "news_portal:news_article:21"

LONG_TEXT = "这是一条很长的舆情正文。" * 40     # 480 字，超过列表截断阈值


class ListContentsTest(unittest.IsolatedAsyncioTestCase):
    async def test_orders_by_published_at_desc(self) -> None:
        """候选列表由原始表按发布时间倒序给出，服务必须保持这个顺序。"""
        hits = [make_raw_hit(WEIBO_1, published_at=1792000300),
                make_raw_hit(WEIBO_2, published_at=1792000100),
                make_raw_hit(DOUYIN_1, published_at=1792000200)]
        contents = {uid: make_content(uid) for uid in (WEIBO_1, WEIBO_2, DOUYIN_1)}
        repo = InMemoryContentRepository([make_analysis(uid)
                                         for uid in (WEIBO_1, WEIBO_2, DOUYIN_1)])

        with patch_raw_layer(hits=hits, contents=contents):
            total, items = await service.list_contents(repo, session=None)

        self.assertEqual(total, 3)
        self.assertEqual([i["content_uid"] for i in items],
                         [WEIBO_1, DOUYIN_1, WEIBO_2])

    async def test_filters_are_combined(self) -> None:
        """主题 + 情感 + 风险组合筛选（FR-42/43 的验收口径）。"""
        hits = [make_raw_hit(uid, published_at=1792000300 - i)
                for i, uid in enumerate((WEIBO_1, WEIBO_2, DOUYIN_1))]
        contents = {uid: make_content(uid) for uid in (WEIBO_1, WEIBO_2, DOUYIN_1)}
        repo = InMemoryContentRepository([
            make_analysis(WEIBO_1, sentiment="negative", risk_level="S", topic_id="tp1"),
            make_analysis(WEIBO_2, sentiment="negative", risk_level="C", topic_id="tp1"),
            make_analysis(DOUYIN_1, sentiment="positive", risk_level="S", topic_id="tp2"),
        ], topics={"tp1": "星耀XPhone15", "tp2": "新能源车购置税减免"})

        with patch_raw_layer(hits=hits, contents=contents):
            total, items = await service.list_contents(
                repo, session=None, topic_id="tp1", sentiment="negative", risk_level="S")

        self.assertEqual(total, 1)
        self.assertEqual(items[0]["content_uid"], WEIBO_1)
        self.assertEqual(items[0]["topic_name"], "星耀XPhone15")

    async def test_suspected_bot_filter(self) -> None:
        hits = [make_raw_hit(WEIBO_1), make_raw_hit(WEIBO_2)]
        contents = {uid: make_content(uid) for uid in (WEIBO_1, WEIBO_2)}
        repo = InMemoryContentRepository([
            make_analysis(WEIBO_1, is_suspected_bot=True),
            make_analysis(WEIBO_2, is_suspected_bot=False),
        ])

        with patch_raw_layer(hits=hits, contents=contents):
            total, items = await service.list_contents(repo, session=None,
                                                       is_suspected_bot=True)

        self.assertEqual(total, 1)
        self.assertTrue(items[0]["is_suspected_bot"])

    async def test_text_is_truncated_with_flag(self) -> None:
        hits = [make_raw_hit(WEIBO_1)]
        contents = {WEIBO_1: make_content(WEIBO_1, text=LONG_TEXT)}
        repo = InMemoryContentRepository([make_analysis(WEIBO_1)])

        with patch_raw_layer(hits=hits, contents=contents):
            _, items = await service.list_contents(repo, session=None)

        self.assertEqual(len(items[0]["text"]), 200)
        self.assertTrue(items[0]["text_truncated"])

    async def test_short_text_not_marked_truncated(self) -> None:
        hits = [make_raw_hit(WEIBO_1)]
        contents = {WEIBO_1: make_content(WEIBO_1, text="很短的一条")}
        repo = InMemoryContentRepository([make_analysis(WEIBO_1)])

        with patch_raw_layer(hits=hits, contents=contents):
            _, items = await service.list_contents(repo, session=None)

        self.assertEqual(items[0]["text"], "很短的一条")
        self.assertFalse(items[0]["text_truncated"])

    async def test_empty_candidates_short_circuit(self) -> None:
        """原始侧没有候选内容时应直接返回空，不去查标签表。"""
        repo = InMemoryContentRepository([make_analysis(WEIBO_1)])

        with patch_raw_layer(hits=[], contents={}):
            total, items = await service.list_contents(repo, session=None,
                                                       keyword="不存在的词")

        self.assertEqual((total, items), (0, []))

    async def test_pagination_slices_in_published_order(self) -> None:
        uids = [f"social_media:weibo_note:{i}" for i in range(1, 6)]
        hits = [make_raw_hit(uid, published_at=1792001000 - i)
                for i, uid in enumerate(uids)]
        contents = {uid: make_content(uid) for uid in uids}
        repo = InMemoryContentRepository([make_analysis(uid) for uid in uids])

        with patch_raw_layer(hits=hits, contents=contents):
            total, first = await service.list_contents(repo, session=None,
                                                       page=1, page_size=2)
            _, second = await service.list_contents(repo, session=None,
                                                    page=3, page_size=2)

        self.assertEqual(total, 5)
        self.assertEqual([i["content_uid"] for i in first], uids[:2])
        self.assertEqual([i["content_uid"] for i in second], [uids[4]])

    async def test_missing_raw_content_is_skipped_not_fatal(self) -> None:
        """标签存在但原始内容已清理：少一条，而不是整个列表 500。"""
        hits = [make_raw_hit(WEIBO_1), make_raw_hit(WEIBO_2)]
        contents = {WEIBO_1: make_content(WEIBO_1)}      # WEIBO_2 的原文缺失
        repo = InMemoryContentRepository([make_analysis(WEIBO_1), make_analysis(WEIBO_2)])

        with patch_raw_layer(hits=hits, contents=contents):
            total, items = await service.list_contents(repo, session=None)

        self.assertEqual(total, 2)                        # 总数按标签数算
        self.assertEqual([i["content_uid"] for i in items], [WEIBO_1])

    async def test_engagement_and_author_are_forwarded(self) -> None:
        hits = [make_raw_hit(DOUYIN_1)]
        contents = {DOUYIN_1: make_content(DOUYIN_1, nickname="数码实验室",
                                           likes=1200, comments=88)}
        repo = InMemoryContentRepository([make_analysis(DOUYIN_1)])

        with patch_raw_layer(hits=hits, contents=contents):
            _, items = await service.list_contents(repo, session=None)

        item = items[0]
        self.assertEqual(item["platform_label"], "短视频")
        self.assertEqual(item["author"]["nickname"], "数码实验室")
        self.assertEqual(item["engagement"]["likes"], 1200)
        self.assertEqual(item["engagement"]["total"], 1288)

    async def test_list_does_not_include_raw_text(self) -> None:
        """列表不返回全文（设计规格 8.2）——否则一页 20 条就是几百 KB。"""
        hits = [make_raw_hit(WEIBO_1)]
        contents = {WEIBO_1: make_content(WEIBO_1, text=LONG_TEXT)}
        repo = InMemoryContentRepository([make_analysis(WEIBO_1)])

        with patch_raw_layer(hits=hits, contents=contents):
            _, items = await service.list_contents(repo, session=None)

        self.assertNotIn("raw_text", items[0])
        self.assertNotIn("modality_assets", items[0])


class MatchSemanticsTest(unittest.TestCase):
    """`search_raw_hits` 的两种匹配方式必须显式区分。

    写错这一个参数的表现是「结果少得离谱」而**不报错**：用正文检索去判定主题归属，
    会把「正文里恰好提到该词」的内容算成命中、把真正属于该主题的内容漏掉
    （联调时实际发生过，候选数从 2533 变成 14）。因此未知取值必须显式失败。
    """

    def test_unknown_match_value_rejected(self) -> None:
        from engines.ingestion.reader import search_raw_hits

        with self.assertRaises(ValueError) as ctx:
            asyncio.run(search_raw_hits(session=None, keyword="x", match="keyword"))

        self.assertIn("未知的匹配方式", str(ctx.exception))

    def test_match_constants_are_distinct(self) -> None:
        from engines.ingestion.reader import MATCH_TEXT, MATCH_TOPIC

        self.assertNotEqual(MATCH_TEXT, MATCH_TOPIC)
        self.assertEqual((MATCH_TEXT, MATCH_TOPIC), ("text", "topic"))

    def test_comment_search_fields_differ_from_content(self) -> None:
        """评论表只有 content 一列正文：套用内容表的 title/desc 会直接报错（实测 503）。"""
        from engines.ingestion.adapters import all_adapters

        for adapter in all_adapters():
            with self.subTest(table=adapter.comment_table):
                self.assertEqual(adapter.comment_search_fields, ("content",))
                self.assertTrue(adapter.search_fields)


class DetailTest(unittest.IsolatedAsyncioTestCase):
    async def test_detail_returns_full_text_and_assets(self) -> None:
        """FR-44：详情给全文与 ASR/OCR 转录（含失败状态）。"""
        class Asset:
            asset_id = "asset-1"
            asset_type = "video"
            parser = "asr_mock"
            parse_status = "success"
            parsed_text = "这是视频的语音转录全文"
            error_message = ""
            origin_url = "http://example.com/v/1"

        contents = {DOUYIN_1: make_content(DOUYIN_1, text=LONG_TEXT,
                                           url="http://example.com/v/1",
                                           source_keyword="星耀XPhone15")}
        repo = InMemoryContentRepository([make_analysis(DOUYIN_1, topic_id="tp1")],
                                         topics={"tp1": "星耀XPhone15"})

        with patch_raw_layer(hits=[make_raw_hit(DOUYIN_1)], contents=contents,
                             assets={DOUYIN_1: [Asset()]}):
            detail = await service.get_content_detail(repo, session=None,
                                                     content_uid=DOUYIN_1)

        self.assertEqual(detail["text"], LONG_TEXT)          # 未截断
        self.assertFalse(detail["text_truncated"])
        self.assertEqual(detail["url"], "http://example.com/v/1")
        self.assertEqual(detail["topic_name"], "星耀XPhone15")
        self.assertEqual(len(detail["modality_assets"]), 1)
        self.assertEqual(detail["modality_assets"][0]["parsed_text"],
                         "这是视频的语音转录全文")

    async def test_detail_parses_risk_factors(self) -> None:
        contents = {WEIBO_1: make_content(WEIBO_1)}
        repo = InMemoryContentRepository([
            make_analysis(WEIBO_1, risk_factors='{"negative": 0.6, "volume": 0.3}')])

        with patch_raw_layer(hits=[make_raw_hit(WEIBO_1)], contents=contents):
            detail = await service.get_content_detail(repo, session=None,
                                                     content_uid=WEIBO_1)

        self.assertEqual(detail["risk_factors"], {"negative": 0.6, "volume": 0.3})

    async def test_broken_risk_factors_do_not_break_detail(self) -> None:
        contents = {WEIBO_1: make_content(WEIBO_1)}
        repo = InMemoryContentRepository([make_analysis(WEIBO_1, risk_factors="{坏 JSON")])

        with patch_raw_layer(hits=[make_raw_hit(WEIBO_1)], contents=contents):
            detail = await service.get_content_detail(repo, session=None,
                                                     content_uid=WEIBO_1)

        self.assertIsNone(detail["risk_factors"])

    async def test_unanalyzed_content_returns_404(self) -> None:
        """没有标签的内容（未打标）不提供详情——分析结论是详情的核心内容。"""
        repo = InMemoryContentRepository([])

        with patch_raw_layer(hits=[make_raw_hit(WEIBO_1)],
                             contents={WEIBO_1: make_content(WEIBO_1)}):
            with self.assertRaises(AppError) as ctx:
                await service.get_content_detail(repo, session=None, content_uid=WEIBO_1)

        self.assertEqual(ctx.exception.code, "BIZ_201")
        self.assertIn("尚未完成分析", ctx.exception.message)

    async def test_analyzed_but_missing_raw_content_returns_404(self) -> None:
        repo = InMemoryContentRepository([make_analysis(WEIBO_1)])

        with patch_raw_layer(hits=[], contents={}):
            with self.assertRaises(AppError) as ctx:
                await service.get_content_detail(repo, session=None, content_uid=WEIBO_1)

        self.assertEqual(ctx.exception.code, "BIZ_201")
        self.assertIn("原始内容不存在", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
