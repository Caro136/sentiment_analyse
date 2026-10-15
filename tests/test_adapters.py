"""平台适配器测试。

分两部分：

1. `AdapterMappingTest` —— 用构造的行验证映射逻辑，不需要数据库；
2. `RealDataSmokeTest` —— **用真实表数据验证字段名**。这部分不可省略：
   适配器里的字段名是可以写错的（比如把微博的 `comments_count` 写成 `comment_count`），
   写错不会抛异常，只会静默取到 0——热度算错、排序错乱，但没人知道为什么。
   只有对着真实数据跑一遍才能发现。
"""

import unittest

from engines.contracts.content import RECORD_COMMENT, RECORD_POST
from engines.contracts.enums import SourceType
from engines.ingestion.adapters import (
    adapter_for_table,
    all_adapters,
    comment_tables,
    content_tables,
    get_adapter,
    to_int,
)
from engines.ingestion.adapters.base import to_str


class ToIntTest(unittest.TestCase):
    def test_plain_number_string(self) -> None:
        self.assertEqual(to_int("1613287"), 1613287)

    def test_none_and_empty(self) -> None:
        self.assertEqual(to_int(None), 0)
        self.assertEqual(to_int(""), 0)
        self.assertEqual(to_int("   "), 0)

    def test_number_types(self) -> None:
        self.assertEqual(to_int(42), 42)
        self.assertEqual(to_int(42.7), 42)

    def test_string_with_separators(self) -> None:
        self.assertEqual(to_int("1,234"), 1234)

    def test_garbage_returns_zero(self) -> None:
        """脏数据不该让整条接入链路崩掉。"""
        self.assertEqual(to_int("暂无数据"), 0)

    def test_to_str_normalizes_none(self) -> None:
        self.assertEqual(to_str(None), "")
        self.assertEqual(to_str("  x  "), "x")


# ---------------------------------------------------------------------------
# 真实表结构的样例行（字段名与 data/sql 交付的 DDL 一致）
# ---------------------------------------------------------------------------

WEIBO_ROW = {
    "id": 1, "user_id": "u388516060715", "nickname": "沉默的日记546",
    "gender": "m", "profile_url": "https://m.weibo.cn/u/388516060715",
    "ip_location": "天津", "add_ts": 1791702243, "last_modify_ts": 1791704272,
    "note_id": 2002783120652714,
    "content": "买了X-Phone 15，充电时发热严重，客服说正常现象",
    "create_time": 1791701786, "create_date_time": "2026-10-11 14:56:26+08:00",
    "liked_count": "133", "comments_count": "15", "shared_count": "49",
    "note_url": "https://m.weibo.cn/detail/2002783120652714",
    "source_keyword": "星耀XPhone15",
}

WEIBO_COMMENT_ROW = {
    "id": 1, "user_id": "2345225534", "nickname": "Bryant曼巴魂", "gender": "m",
    "ip_location": "江苏", "add_ts": 1, "last_modify_ts": 2,
    "comment_id": 5307822269007389, "note_id": 2002783120652714,
    "content": "3+1+2的高考今天结束！祝金榜题名",
    "create_time": 1780995706, "create_date_time": "2026-06-09 09:01:46+08:00",
    "comment_like_count": "68", "sub_comment_count": "3", "parent_comment_id": "",
}

DOUYIN_ROW = {
    "id": 1, "user_id": "1315440687852623", "sec_uid": "MS4wLjABAAAA", "nickname": "贾老师谈股权",
    "user_signature": None, "ip_location": "",
    "aweme_id": 7648947265968639247, "aweme_type": "0",
    "title": "怎么都说今年高考变难了？", "desc": "怎么都说今年高考变难了？#认知 #人性",
    "create_time": 1780916580, "liked_count": "43090", "comment_count": "2417",
    "share_count": "72494", "collected_count": "49603",
    "aweme_url": "https://www.douyin.com/video/7648947265968639247",
    "source_keyword": "高考",
}

DOUYIN_COMMENT_ROW = {
    "id": 1, "user_id": "2053484388028423", "nickname": "VIKI", "ip_location": "广东",
    "comment_id": 7649198470352094001, "aweme_id": 7648947265968639247,
    "content": "学一粒沙，考一片海洋", "create_time": 1780967805,
    "sub_comment_count": "4", "parent_comment_id": "0",
    "like_count": "47", "pictures": "",
}

NEWS_ROW = {
    "id": 1, "media_name": "科技传真", "author": "本报记者",
    "title": "X-Phone 15 发热争议持续，客服称属正常现象引用户不满",
    "content": "近日，多位用户在社交平台反映星耀科技新机存在发热与续航问题。",
    "publish_time": 1791701786, "publish_date_time": "2026-10-11 14:56:26+08:00",
    "url": "https://news.example-portal.com/新闻门户/1.html",
    "read_count": "12345", "comment_count": "67", "share_count": "89",
    "source_keyword": "星耀XPhone15",
}

FORUM_ROW = {
    "id": 1, "thread_id": 8000001, "forum_name": "数码之家", "board": "技术版",
    "author_id": "kol_chip_insider", "author_nickname": "芯片内行",
    "title": "从散热结构看X-Phone 15这次的设计取舍",
    "content": "拆解后可以看到，VC均热板面积比上代缩水了约 18%。",
    "publish_time": 1791701786, "publish_date_time": "2026-10-11 14:56:26+08:00",
    "view_count": "48000", "reply_count": "320", "is_pinned": 0, "is_essence": 1,
    "url": "https://bbs.example-forum.com/thread/8000001.html",
    "source_keyword": "星耀XPhone15",
}


class AdapterMappingTest(unittest.TestCase):
    def test_weibo_mapping(self) -> None:
        content = get_adapter(SourceType.SOCIAL).to_content(WEIBO_ROW)
        self.assertEqual(content.content_uid, "social_media:weibo_note:2002783120652714")
        self.assertEqual(content.source_type, SourceType.SOCIAL)
        self.assertEqual(content.record_type, RECORD_POST)
        self.assertEqual(content.engagement.likes, 133)
        self.assertEqual(content.engagement.comments, 15)
        self.assertEqual(content.engagement.shares, 49)
        self.assertEqual(content.author.nickname, "沉默的日记546")
        self.assertEqual(content.published_at, 1791701786)
        self.assertEqual(content.source_keyword, "星耀XPhone15")
        self.assertEqual(content.title, "")          # 微博没有标题字段

    def test_douyin_mapping(self) -> None:
        content = get_adapter(SourceType.VIDEO).to_content(DOUYIN_ROW)
        self.assertEqual(content.content_uid,
                         "short_video:douyin_aweme:7648947265968639247")
        self.assertEqual(content.engagement.likes, 43090)
        self.assertEqual(content.engagement.comments, 2417)
        self.assertEqual(content.engagement.shares, 72494)
        self.assertEqual(content.engagement.collects, 49603)   # 抖音独有的收藏
        self.assertEqual(content.title, "怎么都说今年高考变难了？")

    def test_engagement_field_names_differ_but_converge(self) -> None:
        """核心验证：两平台评论数字段名不同（comments_count vs comment_count），
        但映射到统一的 engagement.comments。"""
        weibo = get_adapter(SourceType.SOCIAL).to_content(WEIBO_ROW)
        douyin = get_adapter(SourceType.VIDEO).to_content(DOUYIN_ROW)
        self.assertEqual(weibo.engagement.comments, 15)
        self.assertEqual(douyin.engagement.comments, 2417)

    def test_news_mapping(self) -> None:
        content = get_adapter(SourceType.NEWS).to_content(NEWS_ROW)
        self.assertEqual(content.content_uid, "news_portal:news_article:1")
        self.assertEqual(content.title, NEWS_ROW["title"])
        self.assertEqual(content.engagement.likes, 12345)       # read_count → likes
        self.assertEqual(content.engagement.comments, 67)
        self.assertEqual(content.author.nickname, "科技传真")
        self.assertEqual(content.author.verified_type, "媒体")

    def test_forum_mapping_uses_author_fields(self) -> None:
        """论坛的作者字段名与社交/短视频不同（author_id / author_nickname）。"""
        content = get_adapter(SourceType.FORUM).to_content(FORUM_ROW)
        self.assertEqual(content.content_uid, "industry_forum:forum_thread:8000001")
        self.assertEqual(content.author.account_id, "kol_chip_insider")
        self.assertEqual(content.author.nickname, "芯片内行")
        self.assertEqual(content.engagement.likes, 48000)       # view_count → likes
        self.assertEqual(content.engagement.replies, 320)       # reply_count → replies

    def test_comment_mapping_sets_parent_uid(self) -> None:
        content = get_adapter(SourceType.SOCIAL).to_comment(WEIBO_COMMENT_ROW)
        self.assertEqual(content.record_type, RECORD_COMMENT)
        self.assertEqual(content.content_uid,
                         "social_media:weibo_note_comment:5307822269007389")
        self.assertEqual(content.parent_content_uid,
                         "social_media:weibo_note:2002783120652714")
        self.assertEqual(content.engagement.likes, 68)          # comment_like_count

    def test_douyin_comment_like_field_name_differs(self) -> None:
        """微博评论点赞叫 comment_like_count，抖音叫 like_count。"""
        weibo = get_adapter(SourceType.SOCIAL).to_comment(WEIBO_COMMENT_ROW)
        douyin = get_adapter(SourceType.VIDEO).to_comment(DOUYIN_COMMENT_ROW)
        self.assertEqual(weibo.engagement.likes, 68)
        self.assertEqual(douyin.engagement.likes, 47)

    def test_account_info_merged_into_author(self) -> None:
        """原始内容表没有粉丝量，权重信息来自 platform_account。"""
        account = {
            "account_id": "u388516060715", "follower_count": 862000,
            "verified_type": "黄V", "influence_score": 82.5, "ip_location": "北京",
        }
        content = get_adapter(SourceType.SOCIAL).to_content(WEIBO_ROW, account=account)
        self.assertEqual(content.author.follower_count, 862000)
        self.assertEqual(content.author.verified_type, "黄V")
        self.assertTrue(content.author.is_kol)

    def test_account_merge_without_account_is_safe(self) -> None:
        content = get_adapter(SourceType.SOCIAL).to_content(WEIBO_ROW, account=None)
        self.assertEqual(content.author.follower_count, 0)
        self.assertFalse(content.author.is_kol)

    def test_news_lookup_key_is_media_name(self) -> None:
        """新闻表没有账号 ID，只能按媒体名匹配账号表。"""
        adapter = get_adapter(SourceType.NEWS)
        self.assertEqual(adapter.account_lookup_key(NEWS_ROW), "科技传真")

    def test_forum_lookup_key_is_author_id(self) -> None:
        adapter = get_adapter(SourceType.FORUM)
        self.assertEqual(adapter.account_lookup_key(FORUM_ROW), "kol_chip_insider")

    def test_missing_fields_degrade_gracefully(self) -> None:
        """缺字段时不该崩，而是退化成空值——真实数据里 NULL 很常见。"""
        content = get_adapter(SourceType.SOCIAL).to_content({"note_id": 1})
        self.assertEqual(content.business_id, 1)
        self.assertEqual(content.text, "")
        self.assertEqual(content.engagement.total, 0)
        self.assertEqual(content.published_at, 0)


class RegistryTest(unittest.TestCase):
    def test_all_four_sources_registered(self) -> None:
        self.assertEqual(len(all_adapters()), 4)
        self.assertEqual(set(content_tables()),
                         {"weibo_note", "douyin_aweme", "news_article", "forum_thread"})
        self.assertEqual(set(comment_tables()),
                         {"weibo_note_comment", "douyin_aweme_comment",
                          "news_article_comment", "forum_post"})

    def test_lookup_by_table_name(self) -> None:
        self.assertIs(adapter_for_table("weibo_note"),
                      adapter_for_table("weibo_note_comment"))

    def test_unknown_source_raises(self) -> None:
        with self.assertRaises(KeyError):
            get_adapter("unknown_source")

    def test_unknown_table_raises(self) -> None:
        with self.assertRaises(KeyError):
            adapter_for_table("not_a_table")


# ---------------------------------------------------------------------------
# 真实数据冒烟测试：字段名写错时，只有对着真实数据跑才能发现
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
class RealDataSmokeTest(unittest.TestCase):
    """用真实表数据验证适配器的字段映射。"""

    @classmethod
    def setUpClass(cls) -> None:
        import pymysql

        from engines.contracts.config import get_settings

        settings = get_settings()
        cls.conn = pymysql.connect(
            host=settings.db_host, port=settings.db_port, user=settings.db_user,
            password=settings.db_password, database=settings.db_name,
            charset=settings.db_charset,
            cursorclass=pymysql.cursors.DictCursor)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.conn.close()

    def _fetch_one(self, table: str) -> dict:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT * FROM `{table}` LIMIT 1")
            row = cur.fetchone()
        self.assertIsNotNone(row, f"{table} 表为空，无法验证映射")
        return row

    def test_content_tables_map_with_real_data(self) -> None:
        for adapter in all_adapters():
            with self.subTest(table=adapter.content_table):
                row = self._fetch_one(adapter.content_table)
                content = adapter.to_content(row)

                self.assertNotEqual(content.business_id, 0,
                                    f"{adapter.content_table}：业务 ID 未取到，字段名可能写错")
                self.assertTrue(content.display_text,
                                f"{adapter.content_table}：文本为空，正文字段名可能写错")
                self.assertGreater(content.published_at, 0,
                                   f"{adapter.content_table}：时间未取到，字段名可能写错")
                self.assertGreater(content.engagement.total, 0,
                                   f"{adapter.content_table}：互动量全为 0，字段名可能写错")

    def test_comment_tables_map_with_real_data(self) -> None:
        for adapter in all_adapters():
            with self.subTest(table=adapter.comment_table):
                row = self._fetch_one(adapter.comment_table)
                content = adapter.to_comment(row)

                self.assertEqual(content.record_type, RECORD_COMMENT)
                self.assertTrue(content.text, f"{adapter.comment_table}：评论文本为空")
                self.assertTrue(content.parent_content_uid,
                                f"{adapter.comment_table}：父内容标识未取到，关联字段可能写错")
                self.assertIn(":", content.parent_content_uid)

    def test_parent_uid_points_to_existing_content(self) -> None:
        """评论的父内容标识必须能在对应内容表中找到——否则观点聚类会挂到不存在的事件上。"""
        for adapter in all_adapters():
            with self.subTest(table=adapter.comment_table):
                comment_row = self._fetch_one(adapter.comment_table)
                parent_id = to_int(comment_row.get(adapter.parent_field))
                with self.conn.cursor() as cur:
                    cur.execute(
                        f"SELECT COUNT(*) AS n FROM `{adapter.content_table}` "
                        f"WHERE `{adapter.content_business_field}` = %s", (parent_id,))
                    self.assertGreater(cur.fetchone()["n"], 0,
                                       f"{adapter.comment_table} 的父内容不存在")


if __name__ == "__main__":
    unittest.main()
