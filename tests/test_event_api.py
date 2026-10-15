"""事件与溯源接口测试（离线）。

接口层才会暴露的问题：权限（三类角色都能读事件）、枚举筛选的校验、
路径参数（`cluster_id` 里带冒号，如 `星耀XPhone15:cluster_1`）、以及 404 的文案。
"""

import json
import unittest
from unittest.mock import patch

from httpx2 import ASGITransport, AsyncClient

from app.main import create_app
from app.routers.events import get_content_repository, get_event_repository
from app.security import get_user_repository
from app.services.auth import passwords
from engines.common.db.session import get_db_session
from tests.fakes import (
    InMemoryContentRepository,
    InMemoryEventRepository,
    InMemoryUserRepository,
    capture_audit_writes,
    make_analysis,
    make_cluster,
    make_content,
    make_event,
    make_user,
    patch_raw_layer,
)

WEIBO_ORIGIN = "social_media:weibo_note:1"
WEIBO_KOL = "social_media:weibo_note:2"
NEWS_1 = "news_portal:news_article:11"
CLUSTER_ID = "星耀XPhone15:cluster_1"


class EventApiTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        params_patcher = patch.object(passwords, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        params_patcher.start()
        self.addCleanup(params_patcher.stop)

        self.accounts = {
            "admin": make_user("admin", "Admin@123", role="admin"),
            "analyst": make_user("analyst", "Analyst@123", role="analyst"),
            "pr": make_user("pr", "Pr@12345", role="pr"),
        }

        uids = [WEIBO_ORIGIN, WEIBO_KOL, NEWS_1]
        self.contents = {
            WEIBO_ORIGIN: make_content(WEIBO_ORIGIN, published_at=100, text="最早的一条"),
            WEIBO_KOL: make_content(WEIBO_KOL, published_at=300, influence_score=95,
                                    verified_type="黄V", text="引爆的一条", likes=900),
            NEWS_1: make_content(NEWS_1, published_at=200, text="媒体跟进"),
        }
        self.content_repo = InMemoryContentRepository(
            records=[make_analysis(uid, topic_id="tp1", risk_level="S" if uid == WEIBO_KOL
                                   else "C") for uid in uids],
            topics={"tp1": "星耀XPhone15"})
        self.event_repo = InMemoryEventRepository(
            events=[make_event("evt_1", title="星耀XPhone15"),
                    make_event("evt_2", title="味鲜居后厨卫生", status="handling")],
            links={"evt_1": uids, "evt_2": []},
            clusters=[make_cluster(CLUSTER_ID, size=110, label="要求退款并质疑散热")],
            members={CLUSTER_ID: [WEIBO_ORIGIN, WEIBO_KOL]},
            representatives={CLUSTER_ID: [WEIBO_KOL]},
            graph={"nodes": [{"id": "e1", "name": "星耀科技", "type": "机构"}],
                   "edges": [{"source": "e1", "target": "e2", "type": "推出",
                              "weight": 15.0}]},
            entity_counts=(2, 1))

        app = create_app()
        app.dependency_overrides[get_user_repository] = lambda: InMemoryUserRepository(
            list(self.accounts.values()))
        app.dependency_overrides[get_event_repository] = lambda: self.event_repo
        app.dependency_overrides[get_content_repository] = lambda: self.content_repo
        app.dependency_overrides[get_db_session] = lambda: None
        self.app = app

        self.audit_rows: list = []
        audit_patcher = capture_audit_writes(self.audit_rows)
        audit_patcher.start()
        self.addCleanup(audit_patcher.stop)

        raw_layer = patch_raw_layer(hits=[], contents=self.contents)
        raw_layer.__enter__()
        self.addCleanup(raw_layer.__exit__, None, None, None)

        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

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


class EventListApiTest(EventApiTestCase):
    async def test_all_roles_can_list_events(self) -> None:
        for username in ("admin", "analyst", "pr"):
            with self.subTest(user=username):
                await self.login(username)
                response = await self.client.get("/api/v1/events")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["total"], 2)

    async def test_stats_are_computed(self) -> None:
        await self.login("analyst")
        items = (await self.client.get("/api/v1/events")).json()["items"]
        target = next(i for i in items if i["event_id"] == "evt_1")

        self.assertEqual(target["topic_name"], "星耀XPhone15")
        self.assertEqual(target["stats"]["content_count"], 3)
        self.assertEqual(target["stats"]["total_volume"], 900)
        self.assertEqual(target["stats"]["risk_level"], "S")
        self.assertEqual(target["stats"]["first_seen_at"], 100)
        self.assertEqual(target["stats"]["last_seen_at"], 300)
        self.assertEqual(target["cluster_count"], 1)

    async def test_filter_by_status_and_risk(self) -> None:
        await self.login("analyst")

        handling = await self.client.get("/api/v1/events", params={"status": "handling"})
        self.assertEqual(handling.json()["total"], 1)
        self.assertEqual(handling.json()["items"][0]["event_id"], "evt_2")

        high_risk = await self.client.get("/api/v1/events", params={"risk_level": "S"})
        self.assertEqual(high_risk.json()["total"], 1)
        self.assertEqual(high_risk.json()["items"][0]["event_id"], "evt_1")

    async def test_invalid_status_returns_422(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/events", params={"status": "closed"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")

    async def test_detail_includes_graph_scale(self) -> None:
        await self.login("analyst")
        detail = (await self.client.get("/api/v1/events/evt_1")).json()

        self.assertEqual(detail["entity_count"], 2)
        self.assertEqual(detail["relation_count"], 1)
        self.assertEqual(detail["stats"]["content_count"], 3)

    async def test_unknown_event_returns_404(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/events/evt-nope")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "BIZ_201")


class TimelineApiTest(EventApiTestCase):
    async def test_timeline_orders_ascending_with_roles(self) -> None:
        await self.login("analyst")
        body = (await self.client.get("/api/v1/events/evt_1/timeline")).json()

        self.assertEqual(body["ordered_by"], "published_at")
        self.assertEqual(body["role_source"], "derived")
        times = [node["published_at"] for node in body["nodes"]]
        self.assertEqual(times, sorted(times))

        roles = {node["content_uid"]: node["role"] for node in body["nodes"]}
        self.assertEqual(roles[WEIBO_ORIGIN], "origin")
        self.assertEqual(roles[WEIBO_KOL], "ignition")
        self.assertEqual(roles[NEWS_1], "followup")

    async def test_node_scope_all_returns_everything(self) -> None:
        await self.login("analyst")
        body = (await self.client.get("/api/v1/events/evt_1/timeline",
                                      params={"node_scope": "all"})).json()
        self.assertEqual(body["returned"], body["total_contents"])

    async def test_invalid_node_scope_rejected_by_schema(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/events/evt_1/timeline",
                                         params={"node_scope": "everything"})
        self.assertEqual(response.status_code, 422)

    async def test_empty_event_timeline(self) -> None:
        await self.login("analyst")
        body = (await self.client.get("/api/v1/events/evt_2/timeline")).json()
        self.assertEqual(body["nodes"], [])
        self.assertEqual(body["total_contents"], 0)


class ClusterApiTest(EventApiTestCase):
    async def test_clusters_with_representatives(self) -> None:
        await self.login("analyst")
        body = (await self.client.get("/api/v1/events/evt_1/clusters")).json()

        self.assertEqual(body["total"], 1)
        cluster = body["items"][0]
        self.assertEqual(cluster["cluster_id"], CLUSTER_ID)
        self.assertEqual(cluster["size"], 110)
        self.assertEqual(len(cluster["representatives"]), 1)
        self.assertEqual(cluster["representatives"][0]["content_uid"], WEIBO_KOL)

    async def test_members_drilldown_with_colon_in_path(self) -> None:
        """簇 ID 含冒号（`星耀XPhone15:cluster_1`），前端会 encodeURIComponent。"""
        await self.login("analyst")

        plain = await self.client.get(
            f"/api/v1/events/evt_1/clusters/{CLUSTER_ID}/members")
        encoded = await self.client.get(
            "/api/v1/events/evt_1/clusters/%E6%98%9F%E8%80%80XPhone15%3Acluster_1/members")

        for response in (plain, encoded):
            with self.subTest(status=response.status_code):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["total"], 2)

        self.assertEqual(plain.json()["items"][0]["content_uid"], WEIBO_ORIGIN)
        self.assertIn("sentiment", plain.json()["items"][0])      # 与动态流同构

    async def test_unknown_cluster_returns_404(self) -> None:
        await self.login("analyst")
        response = await self.client.get(
            "/api/v1/events/evt_1/clusters/none:cluster_9/members")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "BIZ_201")

    async def test_pr_can_read_clusters(self) -> None:
        await self.login("pr")
        response = await self.client.get("/api/v1/events/evt_1/clusters")
        self.assertEqual(response.status_code, 200)


class RiskOverrideApiTest(EventApiTestCase):
    """FR-24 风险等级人工复核：权限仅分析师、理由必填、双份记录。"""

    def audit_for(self, action: str) -> list:
        return [row for row in self.audit_rows if row.action == action]

    async def test_analyst_can_override_with_reason(self) -> None:
        self.audit_rows.clear()
        await self.login("analyst")
        response = await self.client.put(
            "/api/v1/events/evt_1/risk-level",
            json={"risk_level": "B", "reason": "已核实为个案，非批次问题"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["risk_level"], "B")                  # 生效值 = 人工值
        self.assertEqual(body["algorithm_risk_level"], "S")        # 算法原值仍在
        self.assertTrue(body["is_manual_override"])
        self.assertEqual(body["override_reason"], "已核实为个案，非批次问题")

        row = self.audit_for("risk_override")[-1]
        self.assertEqual(row.target, "event:evt_1")
        self.assertEqual(row.user_id, self.accounts["analyst"].user_id)
        before = json.loads(row.before_value)
        after = json.loads(row.after_value)
        self.assertEqual(before["algorithm_risk_level"], "S")
        self.assertEqual(after["risk_level"], "B")
        self.assertEqual(after["reason"], "已核实为个案，非批次问题")

    async def test_pr_cannot_override(self) -> None:
        await self.login("pr")
        response = await self.client.put(
            "/api/v1/events/evt_1/risk-level",
            json={"risk_level": "B", "reason": "公关想改一下"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_admin_cannot_override(self) -> None:
        """管理员不参与业务结论的修改（PRD 2 / 2026-10-15 确认）。"""
        await self.login("admin")
        response = await self.client.put(
            "/api/v1/events/evt_1/risk-level",
            json={"risk_level": "B", "reason": "管理员想改一下"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_short_reason_rejected_by_schema(self) -> None:
        await self.login("analyst")
        response = await self.client.put("/api/v1/events/evt_1/risk-level",
                                         json={"risk_level": "B", "reason": "改了"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")

    async def test_invalid_level_rejected(self) -> None:
        await self.login("analyst")
        response = await self.client.put(
            "/api/v1/events/evt_1/risk-level",
            json={"risk_level": "X", "reason": "等级写错也该被拦下"})
        self.assertEqual(response.status_code, 422)

    async def test_unknown_event_returns_404(self) -> None:
        await self.login("analyst")
        response = await self.client.put(
            "/api/v1/events/evt-nope/risk-level",
            json={"risk_level": "B", "reason": "不存在的事件"})
        self.assertEqual(response.status_code, 404)

    async def test_repeat_same_level_does_not_rewrite(self) -> None:
        """重复提交同一等级：不写库（历史理由保持首次那份），但这次确认仍进审计。"""
        await self.login("analyst")
        await self.client.put("/api/v1/events/evt_1/risk-level",
                              json={"risk_level": "B", "reason": "首次复核为 B"})

        self.audit_rows.clear()
        second = await self.client.put("/api/v1/events/evt_1/risk-level",
                                       json={"risk_level": "B", "reason": "再次确认为 B"})

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["risk_level"], "B")
        self.assertEqual(second.json()["override_reason"], "首次复核为 B")   # 未被改写

        rows = self.audit_for("risk_override")
        self.assertEqual(len(rows), 1)
        before = json.loads(rows[0].before_value)
        after = json.loads(rows[0].after_value)
        self.assertEqual(before["risk_level"], after["risk_level"])
        self.assertFalse(after["changed"])                    # 明确标注「未发生变更」

    async def test_effective_level_is_used_for_filtering(self) -> None:
        """复核为 B 之后，按 B 筛应能筛到该事件（筛选与展示口径一致）。"""
        await self.login("analyst")
        await self.client.put("/api/v1/events/evt_1/risk-level",
                              json={"risk_level": "B", "reason": "复核为 B 级"})

        by_b = await self.client.get("/api/v1/events", params={"risk_level": "B"})
        by_s = await self.client.get("/api/v1/events", params={"risk_level": "S"})

        self.assertEqual([i["event_id"] for i in by_b.json()["items"]], ["evt_1"])
        self.assertEqual(by_s.json()["total"], 0)


class GraphApiTest(EventApiTestCase):
    async def test_graph_returns_nodes_and_edges(self) -> None:
        await self.login("analyst")
        body = (await self.client.get("/api/v1/events/evt_1/graph")).json()

        self.assertEqual(body["event_id"], "evt_1")
        self.assertEqual([node["name"] for node in body["nodes"]], ["星耀科技"])
        self.assertEqual(body["edges"][0]["type"], "推出")
        self.assertFalse(body["truncated"])

    async def test_unknown_event_returns_404(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/events/evt-nope/graph")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
