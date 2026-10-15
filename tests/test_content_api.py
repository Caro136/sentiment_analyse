"""内容查询接口测试（离线）。

关注三件接口层才会暴露的事：

1. **筛选参数的类型**：写错枚举字面值必须 422（不是静默返回空列表）；
2. **权限**：三类角色都能读（工作台是共同视图），未登录仍被默认拒绝
   （由 `test_auth_api.py` 的覆盖率测试保证，这里只验证角色行为）；
3. **`content_uid` 里的冒号**：路径参数含 `:`，前端会 encodeURIComponent，
   后端要能原样解出（拼错就是 404，而这类错很难一眼看出）。
"""

import unittest
from unittest.mock import patch

from httpx2 import ASGITransport, AsyncClient

from app.main import create_app
from app.routers.contents import get_content_repository
from app.security import get_user_repository
from app.services.auth import passwords
from engines.common.db.session import get_db_session
from tests.fakes import (
    InMemoryContentRepository,
    InMemoryUserRepository,
    capture_audit_writes,
    make_analysis,
    make_content,
    make_raw_hit,
    make_user,
    patch_raw_layer,
)

WEIBO_1 = "social_media:weibo_note:1"
WEIBO_2 = "social_media:weibo_note:2"
DOUYIN_1 = "short_video:douyin_aweme:11"


class ContentApiTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        patcher = patch.object(passwords, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        patcher.start()
        self.addCleanup(patcher.stop)

        self.accounts = {
            "admin": make_user("admin", "Admin@123", role="admin"),
            "analyst": make_user("analyst", "Analyst@123", role="analyst"),
            "pr": make_user("pr", "Pr@12345", role="pr"),
        }

        app = create_app()
        self.contents = InMemoryContentRepository(
            records=[
                make_analysis(WEIBO_1, sentiment="negative", risk_level="S", topic_id="tp1"),
                make_analysis(WEIBO_2, sentiment="positive", risk_level="C", topic_id="tp1"),
                make_analysis(DOUYIN_1, sentiment="negative", risk_level="A",
                              is_suspected_bot=True),
            ],
            topics={"tp1": "星耀XPhone15"})

        app.dependency_overrides[get_user_repository] = lambda: InMemoryUserRepository(
            list(self.accounts.values()))
        app.dependency_overrides[get_content_repository] = lambda: self.contents
        app.dependency_overrides[get_db_session] = lambda: None
        self.app = app
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

        # 登录会写审计。审计走自己的 session_scope（生产是 MySQL），必须拦住：
        # 否则测试会连真实库，而且引擎是模块级单例、事件循环却是每个用例一个，
        # 复用起来会静默出错（实测表现为「审计写入失败 AttributeError」）。
        audit_rows: list = []
        audit_patcher = capture_audit_writes(audit_rows)
        audit_patcher.start()
        self.addCleanup(audit_patcher.stop)

        self.hits = [make_raw_hit(WEIBO_1, published_at=1792000300),
                     make_raw_hit(WEIBO_2, published_at=1792000100),
                     make_raw_hit(DOUYIN_1, published_at=1792000200)]
        # 微博那条刻意写长（超过列表的 200 字截断阈值），用于验证截断与全文的差异
        self.long_text = "散热太差了，冬天都不用买暖手宝。" * 30
        self.raw = {
            WEIBO_1: make_content(WEIBO_1, text=self.long_text),
            WEIBO_2: make_content(WEIBO_2, text="用着还行"),
            DOUYIN_1: make_content(DOUYIN_1, text="实测温度 47.6 度",
                                   url="http://example.com/v/1"),
        }
        raw_layer = patch_raw_layer(hits=self.hits, contents=self.raw)
        raw_layer.__enter__()
        self.addCleanup(raw_layer.__exit__, None, None, None)

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.app.dependency_overrides.clear()

    async def login(self, username: str) -> None:
        response = await self.client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": {"admin": "Admin@123",
                                                     "analyst": "Analyst@123",
                                                     "pr": "Pr@12345"}[username]})
        self.assertEqual(response.status_code, 200, response.text)


class ListApiTest(ContentApiTestCase):
    async def test_all_roles_can_read(self) -> None:
        for username in ("admin", "analyst", "pr"):
            with self.subTest(user=username):
                await self.login(username)
                response = await self.client.get("/api/v1/contents")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["total"], 3)

    async def test_ordered_by_published_at_desc(self) -> None:
        await self.login("analyst")
        items = (await self.client.get("/api/v1/contents")).json()["items"]
        self.assertEqual([i["content_uid"] for i in items],
                         [WEIBO_1, DOUYIN_1, WEIBO_2])

    async def test_combined_filters(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/contents", params={
            "topic_id": "tp1", "source_type": "social_media",
            "sentiment": "negative", "risk_level": "S"})

        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["content_uid"], WEIBO_1)
        self.assertEqual(body["items"][0]["topic_name"], "星耀XPhone15")

    async def test_platform_label_is_localised(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/contents",
                                         params={"source_type": "short_video"})
        item = response.json()["items"][0]
        self.assertEqual(item["platform_label"], "短视频")

    async def test_list_text_truncated_with_flag(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/contents", params={"keyword": "散热"})
        item = response.json()["items"][0]
        self.assertEqual(len(item["text"]), 200)
        self.assertTrue(item["text_truncated"])

    async def test_invalid_enum_returns_422_not_empty_list(self) -> None:
        """写错字面值要显式失败——静默空列表会被当成「这个筛选没数据」。"""
        await self.login("analyst")

        for params in ({"sentiment": "negativee"}, {"risk_level": "X"},
                       {"source_type": "shortvideo"}):
            with self.subTest(params=params):
                response = await self.client.get("/api/v1/contents", params=params)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "BIZ_400")

    async def test_page_size_limit_enforced(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/contents", params={"page_size": 500})
        self.assertEqual(response.status_code, 422)

    async def test_empty_result_has_zero_total(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/contents",
                                         params={"topic_id": "tp-not-exist"})
        self.assertEqual(response.json()["total"], 0)
        self.assertEqual(response.json()["items"], [])


class DetailApiTest(ContentApiTestCase):
    async def test_detail_with_colon_in_path(self) -> None:
        """content_uid 含冒号，前端会 encodeURIComponent；两种写法都要能解。"""
        await self.login("analyst")

        plain = await self.client.get(f"/api/v1/contents/{WEIBO_1}")
        encoded = await self.client.get(f"/api/v1/contents/{DOUYIN_1}".replace(":", "%3A"))

        self.assertEqual(plain.status_code, 200)
        self.assertEqual(encoded.status_code, 200)
        self.assertEqual(encoded.json()["content_uid"], DOUYIN_1)

    async def test_detail_returns_full_text_and_analysis(self) -> None:
        await self.login("analyst")
        body = (await self.client.get(f"/api/v1/contents/{WEIBO_1}")).json()

        self.assertEqual(body["text"], self.long_text)     # 全文（列表只给 200）
        self.assertFalse(body["text_truncated"])
        self.assertEqual(body["sentiment"], "negative")
        self.assertEqual(body["risk_level"], "S")
        self.assertEqual(body["model_version"], "test-model")
        self.assertIn("engagement", body)
        self.assertIn("modality_assets", body)

    async def test_detail_missing_returns_404(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/contents/social_media:weibo_note:999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "BIZ_201")

    async def test_pr_can_read_detail(self) -> None:
        await self.login("pr")
        response = await self.client.get(f"/api/v1/contents/{DOUYIN_1}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["url"], "http://example.com/v/1")


if __name__ == "__main__":
    unittest.main()
