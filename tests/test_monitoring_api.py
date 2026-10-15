"""监测配置接口的端到端测试（离线）。

在真实 FastAPI 应用上驱动，数据库依赖换成内存替身（`tests/fakes.py`）。
重点不是「接口通不通」，而是三件容易做错的事：

1. **权限**：公关连主题都不该改（PRD 2 明确「不可修改监测主题」），
   分析师只能改**本人**主题（数据范围），通道与运行配置只有管理员能动；
2. **审计**：所有写操作都要留痕且带前后值（T-20 的装饰器在此第一次真实使用）；
3. **失败路径的提示**：词包冲突、越界取值、白名单外的键都要给出可读的原因。
"""

import json
import unittest
from unittest.mock import patch

from httpx2 import ASGITransport, AsyncClient

from app.main import create_app
from app.routers.topics import get_monitoring_repository
from app.security import get_user_repository
from app.services.auth import passwords
from engines.common.db.session import get_db_session
from engines.contracts.enums import SourceType
from engines.ingestion.reader import SourceProbe
from tests.fakes import (
    InMemoryMonitoringRepository,
    InMemoryUserRepository,
    capture_audit_writes,
    capture_probe,
    make_user,
)


class _FakeSession:
    """通道自检的会话占位（探测函数在用例里被换成替身）。"""


class MonitoringApiTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # 与 test_auth_api 同一处理：口令哈希的生产参数刻意很慢（约 40ms/次），
        # 每个用例都要建三个账号并登录一次，用生产参数会让本文件跑到 20 秒以上
        patcher = patch.object(passwords, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        patcher.start()
        self.addCleanup(patcher.stop)

        self.admin = make_user("admin", "Admin@123", role="admin")
        self.analyst = make_user("analyst", "Analyst@123", role="analyst")
        self.pr = make_user("pr", "Pr@12345", role="pr")

        app = create_app()
        self.users = InMemoryUserRepository([self.admin, self.analyst, self.pr])
        self.monitoring = InMemoryMonitoringRepository()
        app.dependency_overrides[get_user_repository] = lambda: self.users
        app.dependency_overrides[get_monitoring_repository] = lambda: self.monitoring
        # 通道自检会用到会话；探测函数在用例里被替换，因此这里给个占位对象即可
        app.dependency_overrides[get_db_session] = lambda: _FakeSession()
        self.app = app

        self.audit_rows: list = []
        audit_patcher = capture_audit_writes(self.audit_rows)
        audit_patcher.start()
        self.addCleanup(audit_patcher.stop)

        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.app.dependency_overrides.clear()

    async def login(self, username: str, password: str) -> None:
        response = await self.client.post("/api/v1/auth/login",
                                          json={"username": username, "password": password})
        self.assertEqual(response.status_code, 200, response.text)

    def audit_for(self, action: str) -> list:
        return [row for row in self.audit_rows if row.action == action]

    async def create_topic(self, **overrides):
        payload = {
            "name": "星耀XPhone15",
            "description": "消费电子争议",
            "keywords": [
                {"word": "星耀", "group_type": "brand", "weight": 1.0},
                {"word": "散热", "group_type": "event", "weight": 0.8},
                {"word": "二手", "group_type": "exclude", "weight": 1.0},
            ],
        }
        payload.update(overrides)
        return await self.client.post("/api/v1/topics", json=payload)


class TopicApiTest(MonitoringApiTestCase):
    async def test_analyst_creates_topic_with_keywords(self) -> None:
        await self.login("analyst", "Analyst@123")
        response = await self.create_topic()

        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["source_keyword"], "星耀")     # 从品牌词推导
        self.assertEqual(body["owner_id"], self.analyst.user_id)
        self.assertEqual(body["status"], "enabled")
        self.assertEqual(len(body["keywords"]), 3)
        groups = {g["group_type"]: g["count"] for g in body["keyword_groups"]}
        self.assertEqual(groups, {"brand": 1, "event": 1, "competitor": 0, "exclude": 1})

    async def test_pr_cannot_create_topic(self) -> None:
        """PRD 第 2 章：公关不可修改监测主题，也对应 FR-01/02 的验收条目。"""
        await self.login("pr", "Pr@12345")
        response = await self.create_topic()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "AUTH_003")
        self.assertEqual(self.monitoring.topics, {})

    async def test_topic_creation_is_audited(self) -> None:
        await self.login("analyst", "Analyst@123")
        await self.create_topic()

        row = self.audit_for("config_update")[-1]
        self.assertEqual(row.target, "topic:星耀XPhone15")
        self.assertEqual(row.user_id, self.analyst.user_id)
        self.assertIsNone(row.before_value)                 # 新增没有前值
        after = json.loads(row.after_value)
        self.assertEqual(after["source_keyword"], "星耀")
        self.assertEqual(after["keyword_count"], 3)

    async def test_analyst_can_update_own_topic(self) -> None:
        await self.login("analyst", "Analyst@123")
        created = (await self.create_topic()).json()

        response = await self.client.put(f"/api/v1/topics/{created['topic_id']}",
                                         json={"name": "我改自己的主题"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "我改自己的主题")

    async def test_scope_check_applies_to_non_owner(self) -> None:
        """分析师只能改本人主题（数据范围）：主题挂在他人工下时被拒。"""
        await self.login("admin", "Admin@123")
        created = (await self.create_topic(owner_id=self.admin.user_id)).json()

        await self.login("analyst", "Analyst@123")
        response = await self.client.put(f"/api/v1/topics/{created['topic_id']}",
                                         json={"name": "改名"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_admin_updates_any_topic(self) -> None:
        await self.login("analyst", "Analyst@123")
        created = (await self.create_topic()).json()

        await self.login("admin", "Admin@123")
        response = await self.client.put(f"/api/v1/topics/{created['topic_id']}",
                                        json={"status": "disabled", "name": "已停用主题"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "disabled")

        row = self.audit_for("config_update")[-1]
        self.assertEqual(json.loads(row.before_value)["status"], "enabled")
        self.assertEqual(json.loads(row.after_value)["status"], "disabled")

    async def test_replace_keywords_reports_conflict(self) -> None:
        await self.login("analyst", "Analyst@123")
        created = (await self.create_topic()).json()

        response = await self.client.put(
            f"/api/v1/topics/{created['topic_id']}/keywords",
            json={"keywords": [
                {"word": "星耀", "group_type": "brand"},
                {"word": "星耀", "group_type": "exclude"},
            ]})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")
        self.assertIn("排除词", response.json()["message"])

    async def test_list_topics_is_readable_by_all_roles(self) -> None:
        await self.login("analyst", "Analyst@123")
        await self.create_topic()

        for username, password in (("admin", "Admin@123"), ("pr", "Pr@12345")):
            with self.subTest(user=username):
                await self.login(username, password)
                response = await self.client.get("/api/v1/topics")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["total"], 1)

    async def test_missing_topic_returns_404(self) -> None:
        await self.login("analyst", "Analyst@123")
        response = await self.client.get("/api/v1/topics/tp-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "BIZ_201")

    async def test_replace_channels_links_known_channel(self) -> None:
        await self.login("analyst", "Analyst@123")
        # 主题与通道关联前先让通道就位（列表接口会幂等补齐）
        await self.client.get("/api/v1/channels")
        created = (await self.create_topic()).json()

        response = await self.client.put(
            f"/api/v1/topics/{created['topic_id']}/channels",
            json={"channel_ids": ["ch_social_media"]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["channel_ids"], ["ch_social_media"])

        unknown = await self.client.put(
            f"/api/v1/topics/{created['topic_id']}/channels",
            json={"channel_ids": ["ch_nope"]})
        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(unknown.json()["code"], "BIZ_400")


class ChannelApiTest(MonitoringApiTestCase):
    async def test_list_bootstraps_four_channels(self) -> None:
        await self.login("analyst", "Analyst@123")
        response = await self.client.get("/api/v1/channels")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([c["channel_type"] for c in body], [t.value for t in SourceType])
        self.assertTrue(all(c["enabled"] for c in body))

    async def test_pr_cannot_update_channel(self) -> None:
        await self.login("pr", "Pr@12345")
        response = await self.client.put("/api/v1/channels/ch_short_video",
                                        json={"enabled": False})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_admin_disables_channel_and_records_change(self) -> None:
        await self.login("admin", "Admin@123")
        await self.client.get("/api/v1/channels")
        self.audit_rows.clear()

        response = await self.client.put("/api/v1/channels/ch_short_video",
                                        json={"enabled": False, "poll_interval_min": 60})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["enabled"])
        self.assertEqual(response.json()["poll_interval_min"], 60)

        row = self.audit_for("config_update")[-1]
        self.assertEqual(row.target, "channel:ch_short_video")
        self.assertEqual(json.loads(row.before_value)["enabled"], True)
        self.assertEqual(json.loads(row.after_value)["enabled"], False)

    async def test_out_of_range_interval_rejected(self) -> None:
        await self.login("admin", "Admin@123")
        response = await self.client.put("/api/v1/channels/ch_social_media",
                                        json={"poll_interval_min": 0})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")

    async def test_check_reports_facts(self) -> None:
        await self.login("analyst", "Analyst@123")     # 自检是只读，业务角色可用
        probe = SourceProbe(source_type=SourceType.SOCIAL, content_table="weibo_note",
                            comment_table="weibo_note_comment", content_rows=238,
                            comment_rows=2389, latest_published_at=1792044874)

        with capture_probe(probe):
            response = await self.client.get("/api/v1/channels/check")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 4)
        social = body[0]
        self.assertTrue(social["db_reachable"])
        self.assertEqual(social["content_rows"], 238)
        self.assertEqual(social["latest_published_at"], 1792044874)


class ConfigApiTest(MonitoringApiTestCase):
    async def test_non_admin_cannot_read_config(self) -> None:
        await self.login("analyst", "Analyst@123")
        response = await self.client.get("/api/v1/config")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_admin_reads_whitelist(self) -> None:
        await self.login("admin", "Admin@123")
        response = await self.client.get("/api/v1/config")

        self.assertEqual(response.status_code, 200)
        keys = {item["key"] for item in response.json()["items"]}
        self.assertIn("INTENSITY_W_VOLUME", keys)
        self.assertNotIn("SESSION_SECRET", keys)
        self.assertNotIn("DASHSCOPE_API_KEY", keys)

    async def test_admin_hot_updates_and_change_is_audited(self) -> None:
        await self.login("admin", "Admin@123")
        self.audit_rows.clear()

        response = await self.client.post("/api/v1/config",
                                         json={"updates": {"INTENSITY_W_VOLUME": 0.45}})

        self.assertEqual(response.status_code, 200)
        items = {item["key"]: item for item in response.json()["items"]}
        self.assertEqual(items["INTENSITY_W_VOLUME"]["value"], 0.45)
        self.assertTrue(items["INTENSITY_W_VOLUME"]["overridden"])
        self.assertIn("INTENSITY_W_VOLUME", response.json()["overridden_keys"])

        row = self.audit_for("config_update")[-1]
        self.assertEqual(row.target, "config")
        self.assertEqual(json.loads(row.before_value)["INTENSITY_W_VOLUME"], 0.3)
        self.assertEqual(json.loads(row.after_value)["INTENSITY_W_VOLUME"], 0.45)

    async def test_unknown_key_rejected_with_allowed_list(self) -> None:
        await self.login("admin", "Admin@123")
        response = await self.client.post("/api/v1/config",
                                         json={"updates": {"DB_PASSWORD": "x"}})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")
        self.assertIn("DB_PASSWORD", response.json()["message"])

    async def test_out_of_range_weight_rejected(self) -> None:
        await self.login("admin", "Admin@123")
        response = await self.client.post("/api/v1/config",
                                         json={"updates": {"INTENSITY_W_KOL": 2.5}})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")

    async def test_empty_updates_rejected_by_schema(self) -> None:
        await self.login("admin", "Admin@123")
        response = await self.client.post("/api/v1/config", json={"updates": {}})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")


if __name__ == "__main__":
    unittest.main()
