"""接入与清洗测试。

分三部分：
  - CleaningTest   文本清洗规则（含"哪些必须保留"的反向用例）
  - DedupTest      SimHash 指纹、去重、水军识别
  - ReaderTest     真实数据库读取（集成测试，数据库不可用时跳过）
"""

import asyncio
import unittest

from engines.contracts.content import Author, Engagement, UnifiedContent
from engines.contracts.enums import SourceType
from engines.ingestion.cleaning import (
    clean_text,
    has_meaningful_text,
    normalize_for_hash,
    truncate,
)
from engines.ingestion.dedup import (
    deduplicate,
    detect_bots,
    hamming_distance,
    shingles,
    simhash,
)


# ---------------------------------------------------------------------------
# 文本清洗
# ---------------------------------------------------------------------------

class CleaningTest(unittest.TestCase):
    def test_removes_html_tags(self) -> None:
        self.assertEqual(clean_text("<p>正文</p><br/>内容"), "正文 内容")

    def test_removes_urls(self) -> None:
        text = clean_text("详情见 https://example.com/a?b=1 很有意思")
        self.assertNotIn("http", text)
        self.assertIn("很有意思", text)

    def test_removes_zero_width_characters(self) -> None:
        """零宽字符肉眼不可见，却能让"看起来相同"的文本指纹不同——
        这是水军绕过文本去重的常见手段。"""
        self.assertEqual(clean_text("太离谱​了﻿"), "太离谱了")

    def test_keeps_topic_tags(self) -> None:
        """话题标签承载主题归属，是聚类线索，必须保留。"""
        text = clean_text("这次真的失望 #星耀XPhone15# #数码#")
        self.assertIn("#星耀XPhone15#", text)

    def test_keeps_mentions(self) -> None:
        """@提及包含账号名，对实体抽取有用。"""
        self.assertIn("@科技老张", clean_text("看了 @科技老张 的评测"))

    def test_keeps_emoticons(self) -> None:
        """表情符承载情绪强度：[捂脸] 与 [发怒] 是完全不同的态度。"""
        text = clean_text("这质量我服了[捂脸][发怒]")
        self.assertIn("[捂脸]", text)
        self.assertIn("[发怒]", text)

    def test_collapses_whitespace(self) -> None:
        self.assertEqual(clean_text("多个     空格"), "多个 空格")

    def test_empty_input(self) -> None:
        self.assertEqual(clean_text(""), "")
        self.assertEqual(clean_text(None), "")  # type: ignore[arg-type]

    def test_normalize_strips_punctuation(self) -> None:
        """归一化后，改标点的变体应当一致——否则水军改个感叹号就能绕过指纹。"""
        self.assertEqual(normalize_for_hash("太离谱了！"),
                         normalize_for_hash("太离谱了"))
        self.assertEqual(normalize_for_hash("太 离谱 了"),
                         normalize_for_hash("太离谱了"))

    def test_normalize_lowercases(self) -> None:
        self.assertEqual(normalize_for_hash("ABC"), "abc")

    def test_has_meaningful_text(self) -> None:
        self.assertTrue(has_meaningful_text("退钱"))
        self.assertFalse(has_meaningful_text("！！！"))
        self.assertFalse(has_meaningful_text(""))

    def test_truncate(self) -> None:
        self.assertEqual(truncate("abcdef", 3), "abc…")
        self.assertEqual(truncate("abc", 5), "abc")


# ---------------------------------------------------------------------------
# 去重与水军识别
# ---------------------------------------------------------------------------

def make_content(uid: str, text: str, *, likes: int = 0, published_at: int = 0,
                 account_id: str = "u_default", followers: int = 50) -> UnifiedContent:
    return UnifiedContent(
        content_uid=uid,
        source_type=SourceType.SOCIAL,
        source_table="weibo_note",
        business_id=abs(hash(uid)) % 100000,
        author=Author(account_id=account_id, nickname="测试用户",
                      follower_count=followers),
        text=text,
        published_at=published_at,
        engagement=Engagement(likes=likes),
    )


class SimHashTest(unittest.TestCase):
    def test_identical_text_same_fingerprint(self) -> None:
        self.assertEqual(hamming_distance(simhash("这是一条测试内容"),
                                          simhash("这是一条测试内容")), 0)

    def test_punctuation_variant_is_near_duplicate(self) -> None:
        """改标点的变体必须判定为近重复。"""
        distance = hamming_distance(simhash("星耀这波太让人失望了"),
                                    simhash("星耀这波太让人失望了！"))
        self.assertLessEqual(distance, 3)

    def test_different_text_far_apart(self) -> None:
        distance = hamming_distance(simhash("星耀手机发热严重"),
                                    simhash("今天天气不错适合出门走走"))
        self.assertGreater(distance, 3)

    def test_empty_text_fingerprint_is_zero(self) -> None:
        self.assertEqual(simhash(""), 0)
        self.assertEqual(simhash("！！！"), 0)

    def test_shingles_of_short_text(self) -> None:
        self.assertEqual(shingles("ab"), ["ab"])
        self.assertEqual(shingles(""), [])

    def test_shingles_are_bounded_by_step(self) -> None:
        """步长 2 让分片数是文本长度的一半，长文本上开销可控。"""
        tokens = shingles("一" * 100)
        self.assertLess(len(tokens), 60)


class DeduplicateTest(unittest.TestCase):
    def test_exact_duplicates_merged(self) -> None:
        contents = [
            make_content("a", "星耀这波太让人失望了", likes=10),
            make_content("b", "星耀这波太让人失望了", likes=99),
        ]
        result = deduplicate(contents)
        self.assertEqual(len(result.unique_contents), 1)
        self.assertEqual(result.duplicate_count, 1)

    def test_keeps_higher_engagement_copy(self) -> None:
        """保留互动量高的：复制品通常互动量远低于原文。"""
        contents = [
            make_content("low", "星耀这波太让人失望了", likes=1),
            make_content("high", "星耀这波太让人失望了", likes=500),
        ]
        result = deduplicate(contents)
        self.assertEqual(result.unique_contents[0].content_uid, "high")
        self.assertEqual(result.duplicate_of["low"], "high")

    def test_near_duplicates_merged(self) -> None:
        """水军刷量的典型形态：同一句话改标点、加空格。"""
        contents = [
            make_content("a", "味鲜居太脏了，大家都别去了"),
            make_content("b", "味鲜居太脏了，大家都别去了！"),
            make_content("c", "味鲜居太脏了，大家都别去了！！"),
        ]
        result = deduplicate(contents)
        self.assertEqual(len(result.unique_contents), 1)
        self.assertEqual(result.duplicate_count, 2)
        self.assertEqual(len(result.groups), 1)
        self.assertEqual(result.groups[0].size, 3)

    def test_distinct_contents_kept(self) -> None:
        contents = [
            make_content("a", "手机发热严重，玩游戏半小时就烫手"),
            make_content("b", "续航也不太行，半天就没电了"),
            make_content("c", "客服态度还不错，给我换了新机"),
        ]
        result = deduplicate(contents)
        self.assertEqual(len(result.unique_contents), 3)
        self.assertEqual(result.duplicate_count, 0)

    def test_output_preserves_input_order(self) -> None:
        contents = [
            make_content("a", "内容甲"),
            make_content("b", "内容乙完全不同的一段话"),
            make_content("c", "内容甲"),
        ]
        result = deduplicate(contents)
        uids = [c.content_uid for c in result.unique_contents]
        self.assertEqual(uids, ["a", "b"])

    def test_fingerprints_returned_for_all(self) -> None:
        """指纹要落库（content_analysis.simhash），被合并的也要有。"""
        contents = [make_content("a", "内容甲"), make_content("b", "内容甲")]
        result = deduplicate(contents)
        self.assertIn("a", result.fingerprints)
        self.assertIn("b", result.fingerprints)

    def test_empty_input(self) -> None:
        result = deduplicate([])
        self.assertEqual(result.unique_contents, [])
        self.assertEqual(result.duplicate_count, 0)

    def test_comment_texts_also_deduplicated(self) -> None:
        """水军刷的不只是内容，评论同样是重灾区。"""
        contents = [
            make_content("c1", "支持，必须严查", likes=2),
            make_content("c2", "支持，必须严查！", likes=1),
        ]
        self.assertEqual(len(deduplicate(contents).unique_contents), 1)


class DetectBotsTest(unittest.TestCase):
    def test_detects_account_with_repeated_similar_posts(self) -> None:
        contents = [
            make_content(f"b{i}", "星耀X-Phone 15就是个坑，大家千万别买" + "！" * i,
                         published_at=1000 + i * 10, account_id="bot_001", followers=5)
            for i in range(5)
        ]
        result = deduplicate(contents)
        signals = detect_bots(contents, result.fingerprints)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].account_id, "bot_001")
        self.assertGreaterEqual(signals[0].similar_pairs, 3)

    def test_does_not_flag_high_follower_account(self) -> None:
        """万粉账号连续发相似内容更可能是转发新闻，不该被标成水军。"""
        contents = [
            make_content(f"k{i}", "星耀X-Phone 15就是个坑，大家千万别买" + "！" * i,
                         published_at=1000 + i * 10, account_id="kol_001", followers=500000)
            for i in range(5)
        ]
        result = deduplicate(contents)
        self.assertEqual(detect_bots(contents, result.fingerprints), [])

    def test_does_not_flag_normal_user(self) -> None:
        """正常用户短时间内连续发言，但内容各不相同。"""
        contents = [
            make_content("n1", "刚拿到手机，外观还不错", published_at=1000,
                         account_id="user_a"),
            make_content("n2", "但是玩了一会确实有点热", published_at=1100,
                         account_id="user_a"),
            make_content("n3", "准备联系客服问一下", published_at=1200,
                         account_id="user_a"),
            make_content("n4", "客服说要等固件更新", published_at=1300,
                         account_id="user_a"),
        ]
        result = deduplicate(contents)
        self.assertEqual(detect_bots(contents, result.fingerprints), [])

    def test_respects_time_window(self) -> None:
        """超出时间窗的相似内容不算刷量——那是重复搬运，不是集中刷屏。"""
        contents = [
            make_content(f"t{i}", "完全相同的一段刷量文本内容",
                         published_at=1000 + i * 7200,   # 每条间隔 2 小时
                         account_id="bot_002", followers=3)
            for i in range(5)
        ]
        result = deduplicate(contents)
        signals = detect_bots(contents, result.fingerprints, window_minutes=30)
        self.assertEqual(signals, [])

    def test_empty_input(self) -> None:
        self.assertEqual(detect_bots([], {}), [])


# ---------------------------------------------------------------------------
# 真实数据库读取
# ---------------------------------------------------------------------------

def _db_available() -> bool:
    try:
        import pymysql

        from engines.contracts.config import get_settings

        settings = get_settings()
        conn = pymysql.connect(
            host=settings.db_host, port=settings.db_port, user=settings.db_user,
            password=settings.db_password, database=settings.db_name,
            connect_timeout=2)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


DB_READY = _db_available()
SKIP_REASON = "数据库不可用或未初始化（先执行 python -m engines.common.db.init_db --with-data）"


@unittest.skipUnless(DB_READY, SKIP_REASON)
class ReaderTest(unittest.TestCase):
    """读取真实数据。用数据集里的场景关键词。"""

    KEYWORD = "星耀XPhone15"

    def _fetch(self, **kwargs):
        from engines.ingestion.reader import FetchQuery, fetch_contents

        async def run():
            from engines.common.db.session import dispose_engine
            try:
                return await fetch_contents(FetchQuery(keyword=self.KEYWORD, **kwargs))
            finally:
                await dispose_engine()

        return asyncio.run(run())

    @classmethod
    def tearDownClass(cls) -> None:
        # 避免连接池在事件循环关闭后才析构
        from engines.common.db.session import reset_engine

        reset_engine()

    def test_reads_content_and_comments(self) -> None:
        result = self._fetch()
        self.assertGreater(result.content_rows, 0, "未取到内容")
        self.assertGreater(result.comment_rows, 0, "未取到评论")
        self.assertEqual(len(result.contents),
                         result.content_rows + result.comment_rows)

    def test_all_four_sources_present(self) -> None:
        """该主题的数据应当覆盖四类信源——PRD D-03 要求。"""
        result = self._fetch()
        sources = {c.source_type for c in result.contents}
        self.assertEqual(sources, {
            SourceType.SOCIAL, SourceType.VIDEO,
            SourceType.NEWS, SourceType.FORUM,
        })

    def test_comments_carry_parent_uid(self) -> None:
        result = self._fetch()
        comments = [c for c in result.contents if c.record_type == "comment"]
        self.assertTrue(comments)
        for comment in comments[:20]:
            self.assertTrue(comment.parent_content_uid)

    def test_account_weight_merged(self) -> None:
        """账号权重来自 platform_account 表——原始表本身没有粉丝量。"""
        result = self._fetch()
        self.assertTrue(any(c.author.follower_count > 0 for c in result.contents),
                        "所有内容的粉丝量都是 0，账号信息可能没关联上")
        self.assertTrue(any(c.author.is_kol for c in result.contents),
                        "没有识别出任何 KOL，影响力评分可能没取到")

    def test_time_window_filters(self) -> None:
        """时间窗过滤生效，且内容主体严格落在窗口内。

        评论需要单独放宽：过滤条件作用在**父内容**的发布时间上（评论表没有主题字段，
        只能 JOIN 内容表），而评论自身的发布时间晚于内容——但生成数据时带 ±45 秒抖动，
        所以边界附近的评论可能比窗口起点略早。这是数据特性，不是过滤失效。
        """
        all_result = self._fetch()
        times = [c.published_at for c in all_result.contents if c.published_at]
        self.assertTrue(times)

        midpoint = (min(times) + max(times)) // 2
        filtered = self._fetch(start_time=midpoint)
        self.assertLess(len(filtered.contents), len(all_result.contents))

        posts = [c for c in filtered.contents if c.record_type == "post"]
        self.assertTrue(posts, "过滤后没有内容主体，时间窗可能过滤过度")
        self.assertTrue(all(c.published_at >= midpoint for c in posts),
                        "内容主体的发布时间超出了窗口起点")

        comments = [c for c in filtered.contents if c.record_type == "comment"]
        self.assertTrue(all(c.published_at >= midpoint - 120 for c in comments),
                        "评论的发布时间明显早于窗口起点，父内容过滤可能失效")

    def test_limit_per_table(self) -> None:
        result = self._fetch(limit_per_table=5)
        self.assertLessEqual(result.content_rows, 5 * 4)

    def test_unknown_keyword_returns_empty(self) -> None:
        from engines.ingestion.reader import FetchQuery, fetch_contents

        async def run():
            from engines.common.db.session import dispose_engine
            try:
                return await fetch_contents(
                    FetchQuery(keyword="不存在的主题关键词xyz"))
            finally:
                await dispose_engine()

        result = asyncio.run(run())
        self.assertEqual(len(result.contents), 0)


if __name__ == "__main__":
    unittest.main()
