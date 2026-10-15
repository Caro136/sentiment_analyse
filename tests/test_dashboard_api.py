"""大盘接口与物化流程测试（离线）。

接口层关心四件事：权限（三方都能看）、**读路径自愈**（物化缺失/过期时同步补算）、
主题维度（切主题不该是空图）、以及物化表的 upsert 语义（同一指标只留最新一份）。
"""

import json
import time
import unittest
from collections import Counter
from unittest.mock import patch

from httpx2 import ASGITransport, AsyncClient

from app.main import create_app
from app.routers.dashboard import get_dashboard_repository
from app.security import get_user_repository
from app.services.auth import passwords
from app.services.dashboard import service as dashboard_service
from app.services.dashboard.metrics import AlertRow, MetricRow
from app.services.dashboard.repository import SCOPE_ALL, MetricSnapshot
from engines.common.db.session import get_db_session
from tests.fakes import (
    InMemoryUserRepository,
    capture_audit_writes,
    make_user,
    silence_app_logging,
)


class FakeDashboardRepository:
    """内存实现：只做取数聚合与物化读写，指标计算走真实的纯函数。"""

    def __init__(self) -> None:
        self.rows = [
            MetricRow("a", "social_media", "negative", "S", "tp1", 1_792_044_000),
            MetricRow("b", "short_video", "negative", "A", "tp1", 1_792_044_600),
            MetricRow("c", "news_portal", "neutral", "C", "tp2", 1_792_048_000),
            MetricRow("d", "social_media", "positive", "C", None, 1_792_048_600),
        ]
        self.alerts = [AlertRow("S", "unread", "evt_1"), AlertRow("C", "closed", "evt_2")]
        self.open_tickets = 1
        self.metrics: dict[tuple[str, str, str], MetricSnapshot] = {}
        self.upserts = 0

    async def analysis_rows(self, *, topic_id=None, start=None):
        return [row for row in self.rows
                if (not topic_id or row.topic_id == topic_id)
                and (start is None or row.analyzed_at >= start)]

    async def alert_rows(self, *, since=None):
        return list(self.alerts)

    async def open_ticket_count(self):
        return self.open_tickets

    async def entity_mentions(self, *, topic_id=None):
        return Counter({"星耀科技": 12, "散热争议": 8})

    async def keyword_mentions(self, *, topic_id=None):
        return Counter({"星耀XPhone15": 2})

    async def read_metric(self, metric_key, *, scope, bucket):
        return self.metrics.get((metric_key, scope, bucket))

    async def read_scope(self, scope):
        return [snapshot for (key, item_scope, _), snapshot in self.metrics.items()
                if item_scope == scope]

    async def upsert_metric(self, metric_key, *, scope, bucket, payload, ttl_seconds):
        self.upserts += 1
        now = int(time.time())
        snapshot = MetricSnapshot(metric_key=metric_key, scope=scope, bucket=bucket,
                                  payload=json.loads(json.dumps(payload)),
                                  computed_at=now, expires_at=now + ttl_seconds)
        self.metrics[(metric_key, scope, bucket)] = snapshot
        return snapshot


class DashboardServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repo = FakeDashboardRepository()

    async def test_first_read_materialises_metrics(self) -> None:
        """物化表为空时读路径要自愈，而不是返回一屏空图。"""
        view = await dashboard_service.get_metrics(self.repo)
        overview = view["metrics"]["overview"]

        self.assertEqual(overview["total_volume"], 4)
        self.assertEqual(overview["negative_ratio"], 0.5)
        self.assertEqual(overview["high_risk_content_count"], 2)
        self.assertEqual(overview["open_alert_count"], 1)
        self.assertEqual(overview["open_ticket_count"], 1)
        self.assertEqual(self.repo.upserts, len(dashboard_service.METRIC_KEYS))

    async def test_second_read_uses_cache(self) -> None:
        await dashboard_service.get_metrics(self.repo)
        upserts_after_first = self.repo.upserts

        view = await dashboard_service.get_metrics(self.repo)
        self.assertEqual(self.repo.upserts, upserts_after_first)     # 未重复计算
        self.assertEqual(view["metrics"]["overview"]["total_volume"], 4)

    async def test_expired_metrics_are_recomputed(self) -> None:
        await dashboard_service.get_metrics(self.repo)
        for key, snapshot in list(self.repo.metrics.items()):
            self.repo.metrics[key] = MetricSnapshot(
                metric_key=snapshot.metric_key, scope=snapshot.scope,
                bucket=snapshot.bucket, payload=snapshot.payload,
                computed_at=snapshot.computed_at, expires_at=int(time.time()) - 1)

        before = self.repo.upserts
        await dashboard_service.get_metrics(self.repo)
        self.assertGreater(self.repo.upserts, before)

    async def test_force_refresh_recomputes(self) -> None:
        await dashboard_service.get_metrics(self.repo)
        before = self.repo.upserts
        await dashboard_service.get_metrics(self.repo, force_refresh=True)
        self.assertGreater(self.repo.upserts, before)

    async def test_topic_scope_has_its_own_metrics(self) -> None:
        """切主题不该是空图：主题维度的指标必须单独物化。"""
        await dashboard_service.get_metrics(self.repo, topic_id="tp1")
        await dashboard_service.get_metrics(self.repo)

        topic_view = await dashboard_service.get_metrics(self.repo, topic_id="tp1")
        all_view = await dashboard_service.get_metrics(self.repo)

        self.assertEqual(topic_view["scope"], "tp1")
        self.assertEqual(topic_view["metrics"]["overview"]["total_volume"], 2)
        self.assertEqual(all_view["metrics"]["overview"]["total_volume"], 4)
        self.assertIn(("overview", "tp1", "static"), self.repo.metrics)
        self.assertIn(("overview", SCOPE_ALL, "static"), self.repo.metrics)

    async def test_trend_is_materialised_with_bucket(self) -> None:
        await dashboard_service.get_metrics(self.repo)
        trend = (await dashboard_service.get_metrics(self.repo))["metrics"]["volume_trend"]
        self.assertEqual(trend["bucket"], "hour")
        self.assertTrue(trend["points"])

    async def test_trend_window_shrinks_to_data_range(self) -> None:
        """窗口远大于数据跨度时，曲线不能给出一百多个空桶。

        本项目的标签是批量补跑的（全部集中在几分钟内），硬套 7 天窗口会让曲线
        变成「一根尖峰 + 168 个 0」，既没信息量又把 payload 撑大几十倍。
        """
        await dashboard_service.get_metrics(self.repo)
        trend = (await dashboard_service.get_metrics(self.repo))["metrics"]["volume_trend"]

        buckets = [point["bucket_start"] for point in trend["points"]]
        first_row = min(row.analyzed_at for row in self.repo.rows)
        self.assertGreaterEqual(min(buckets), first_row // 3600 * 3600)
        # 数据跨度不到 2 小时 → 桶数应与之相当，而不是 168 个
        self.assertLess(len(trend["points"]), 30)

    async def test_trend_without_data_falls_back_to_window(self) -> None:
        empty = FakeDashboardRepository()
        empty.rows = []
        await dashboard_service.get_metrics(empty)
        trend = (await dashboard_service.get_metrics(empty))["metrics"]["volume_trend"]
        self.assertGreater(len(trend["points"]), 0)      # 无数据时仍给完整窗口的 0 线

    async def test_hot_words_from_structured_sources(self) -> None:
        view = await dashboard_service.get_metrics(self.repo)
        words = [item["word"] for item in view["metrics"]["hot_words"]["items"]]
        self.assertIn("星耀科技", words)
        self.assertIn("星耀XPhone15", words)

    async def test_refresh_all_covers_topics(self) -> None:
        result = await dashboard_service.refresh_all(self.repo,
                                                     topic_ids=["tp1", "tp2"])
        self.assertEqual(set(result), {"all", "tp1", "tp2"})
        for scope in ("all", "tp1", "tp2"):
            self.assertEqual(result[scope], len(dashboard_service.METRIC_KEYS))


class DashboardApiTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        logging_patcher = silence_app_logging()
        logging_patcher.start()
        self.addCleanup(logging_patcher.stop)

        params_patcher = patch.object(passwords, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        params_patcher.start()
        self.addCleanup(params_patcher.stop)

        self.accounts = {
            "admin": make_user("admin", "Admin@123", role="admin"),
            "analyst": make_user("analyst", "Analyst@123", role="analyst"),
            "pr": make_user("pr", "Pr@12345", role="pr"),
        }
        self.repo = FakeDashboardRepository()

        app = create_app()
        app.dependency_overrides[get_user_repository] = lambda: InMemoryUserRepository(
            list(self.accounts.values()))
        app.dependency_overrides[get_dashboard_repository] = lambda: self.repo
        app.dependency_overrides[get_db_session] = lambda: None
        self.app = app

        self.audit_rows: list = []
        audit_patcher = capture_audit_writes(self.audit_rows)
        audit_patcher.start()
        self.addCleanup(audit_patcher.stop)

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


class DashboardApiTest(DashboardApiTestCase):
    async def test_all_roles_can_read(self) -> None:
        for username in ("admin", "analyst", "pr"):
            with self.subTest(user=username):
                await self.login(username)
                response = await self.client.get("/api/v1/dashboard/metrics")
                self.assertEqual(response.status_code, 200)
                self.assertIn("overview", response.json()["metrics"])

    async def test_payload_shape(self) -> None:
        await self.login("analyst")
        body = (await self.client.get("/api/v1/dashboard/metrics")).json()

        self.assertEqual(body["scope"], "all")
        self.assertIsNone(body["topic_id"])
        self.assertGreater(body["computed_at"], 0)
        self.assertEqual(set(body["metrics"]),
                         {"overview", "volume_trend", "sentiment_distribution",
                          "source_distribution", "hot_words"})

    async def test_topic_filter(self) -> None:
        await self.login("analyst")
        body = (await self.client.get("/api/v1/dashboard/metrics",
                                      params={"topic_id": "tp1"})).json()
        self.assertEqual(body["topic_id"], "tp1")
        self.assertEqual(body["metrics"]["overview"]["total_volume"], 2)

    async def test_invalid_hours_rejected(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/dashboard/metrics",
                                         params={"hours": 0})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")

    async def test_unauthenticated_rejected(self) -> None:
        response = await self.client.get("/api/v1/dashboard/metrics")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "AUTH_001")

    async def test_no_audit_for_read_only_metrics(self) -> None:
        """大盘是纯读接口：不该在审计表里留痕（审计只记关键写操作）。"""
        await self.login("analyst")
        self.audit_rows.clear()
        await self.client.get("/api/v1/dashboard/metrics")
        self.assertEqual(self.audit_rows, [])


if __name__ == "__main__":
    unittest.main()
