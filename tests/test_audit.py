"""审计装饰器与落库的测试（FR-04）。

重点不在「成功会写一行」，而在几个容易做错的地方：

1. **失败必须留痕**（口令错误、被业务规则拒绝）——这是审计的核心价值，
   而实现上最容易漏：异常路径往往直接 return 了。
2. **审计与业务事务解耦**——业务回滚不能连带抹掉审计记录。
3. **异常照常抛出**——审计是旁路，不能把业务的失败变成「静默成功」。
4. **上下文不串**——同一请求内连续两次审计，后一次不能带上一次的变更值。
5. **不落敏感值**——口令哈希永远不进审计表。
"""

import asyncio
import json
import unittest

from app import observability
from app.errors import AppError, not_found
from app.services.audit import audit, note_change, write_entry
from app.services.audit import context as audit_context
from app.services.audit import writer
from app.services.audit.context import AuditChange
from engines.contracts.enums import AuditAction
from tests.fakes import capture_audit_writes


class AuditTestCase(unittest.IsolatedAsyncioTestCase):
    """把审计写入收集到 `self.rows`，全程不碰数据库。"""

    async def asyncSetUp(self) -> None:
        self.rows: list = []
        patcher = capture_audit_writes(self.rows)
        patcher.start()
        self.addCleanup(patcher.stop)

    def set_actor(self, user_id: str | None, ip: str | None = None) -> None:
        token = audit_context.bind_actor(user_id)
        self.addCleanup(audit_context.reset_actor, token)
        if ip is not None:
            # 来源 IP 的上下文归 observability（请求事实），审计只是读它
            ip_token = observability.bind_client_ip(ip)
            self.addCleanup(observability.reset_client_ip, ip_token)

    def last(self):
        self.assertTrue(self.rows, "没有任何审计记录")
        return self.rows[-1]


class DecoratorTest(AuditTestCase):
    async def test_success_records_action_target_actor_ip(self) -> None:
        self.set_actor("u-admin", ip="10.0.0.8")

        @audit(AuditAction.USER_UPDATE, target="user:{user_id}")
        async def update_user(user_id: str) -> str:
            return user_id

        self.assertEqual(await update_user("u-1"), "u-1")

        row = self.last()
        self.assertEqual(row.action, "user_update")
        self.assertEqual(row.target, "user:u-1")
        self.assertEqual(row.user_id, "u-admin")
        self.assertEqual(row.ip, "10.0.0.8")
        self.assertTrue(row.operated_at)

    async def test_target_renders_positional_arguments(self) -> None:
        """调用点常用位置参数，模板必须仍能取到值（按签名绑定形参名）。"""
        self.set_actor("u-admin")

        @audit(AuditAction.USER_CREATE, target="username:{username}")
        async def create(repo, username: str, *, role: str) -> None:
            return None

        await create(object(), "newbie", role="pr")
        self.assertEqual(self.last().target, "username:newbie")

    async def test_change_is_recorded(self) -> None:
        self.set_actor("u-admin")

        @audit(AuditAction.USER_UPDATE, target="user:{user_id}")
        async def update(user_id: str) -> None:
            note_change(before={"role": "analyst"}, after={"role": "admin"})

        await update("u-1")

        row = self.last()
        self.assertEqual(json.loads(row.before_value), {"role": "analyst"})
        self.assertEqual(json.loads(row.after_value), {"role": "admin"})

    async def test_failure_is_recorded_and_reraised(self) -> None:
        """业务失败要留痕，同时异常必须原样抛出——审计不能吞掉失败。"""
        self.set_actor("u-admin")

        @audit(AuditAction.USER_UPDATE, target="user:{user_id}")
        async def update(user_id: str) -> None:
            raise not_found("用户不存在")

        with self.assertRaises(AppError):
            await update("u-missing")

        row = self.last()
        self.assertEqual(row.action, "user_update")
        self.assertEqual(row.target, "user:u-missing")
        payload = json.loads(row.after_value)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "BIZ_201")     # 记错误码，不记异常原文

    async def test_unexpected_exception_records_type_name(self) -> None:
        self.set_actor("u-admin")

        @audit(AuditAction.LOGIN, target="username:{username}")
        async def failing(username: str) -> None:
            raise RuntimeError("内部细节不应进审计表")

        with self.assertRaises(RuntimeError):
            await failing("someone")

        payload = json.loads(self.last().after_value)
        self.assertEqual(payload["error"], "RuntimeError")
        self.assertNotIn("内部细节", self.last().after_value)

    async def test_change_does_not_leak_between_calls(self) -> None:
        """连续两次审计：第二次不能带上第一次的前后值。"""
        self.set_actor("u-admin")

        @audit(AuditAction.USER_UPDATE, target="user:{user_id}")
        async def update(user_id: str, *, with_change: bool) -> None:
            if with_change:
                note_change(before={"role": "analyst"}, after={"role": "admin"})

        await update("u-1", with_change=True)
        await update("u-2", with_change=False)

        self.assertEqual(self.rows[0].target, "user:u-1")
        self.assertEqual(self.rows[1].target, "user:u-2")
        self.assertIsNone(self.rows[1].before_value.strip() if self.rows[1].before_value else None)

    async def test_actor_absent_records_null(self) -> None:
        """登录失败时操作人未知（用户都不存在），user_id 允许为空。"""

        @audit(AuditAction.LOGIN, target="username:{username}")
        async def login(username: str, password: str) -> None:
            raise AppError("AUTH_004")

        with self.assertRaises(AppError):
            await login("nobody", "x")

        self.assertIsNone(self.last().user_id)
        self.assertEqual(self.last().target, "username:nobody")

    async def test_explicit_actor_overrides_context(self) -> None:
        self.set_actor("u-ctx")

        @audit(AuditAction.LOGIN, target="username:{username}")
        async def login(username: str, password: str) -> None:
            audit_context.bind_actor("u-explicit")

        await login("someone", "x")
        self.assertEqual(self.last().user_id, "u-explicit")

    async def test_missing_template_key_does_not_break_business(self) -> None:
        self.set_actor("u-admin")

        @audit(AuditAction.USER_UPDATE, target="user:{nonexistent}")
        async def update(user_id: str) -> str:
            return "done"

        self.assertEqual(await update("u-1"), "done")
        self.assertEqual(self.last().target, "user:{nonexistent}")   # 保留占位原文

    async def test_note_target_overrides_template(self) -> None:
        """目标只有执行后才知道时，函数内登记的目标优先。

        实测踩过：新建报告的 `report_id` 是函数内部生成的，装饰器的
        `target="report:{report_id}"` 渲染不出来，审计行里留的是字面占位符。
        """
        from app.services.audit.context import note_target

        self.set_actor("u-admin")

        @audit(AuditAction.REPORT_GENERATE, target="report_create")
        async def create() -> str:
            note_target("report:rpt123")
            return "rpt123"

        await create()
        self.assertEqual(self.last().target, "report:rpt123")

    def test_unknown_action_rejected_at_decoration(self) -> None:
        """动作名写错会让审计查询永远筛不到，必须在定义时就炸而不是运行时静默。"""
        with self.assertRaises(ValueError):
            audit("user_udpate")            # 注意：拼错的动作名

    def test_sync_function_rejected(self) -> None:
        with self.assertRaises(TypeError):
            @audit(AuditAction.LOGIN)
            def not_async() -> None: ...    # noqa: E704

    async def test_concurrent_calls_keep_their_own_change(self) -> None:
        """并发调用各带各的前后值：contextvar 按任务隔离。"""
        self.set_actor("u-admin")

        @audit(AuditAction.USER_UPDATE, target="user:{user_id}")
        async def update(user_id: str) -> None:
            await asyncio.sleep(0)
            note_change(before={"u": user_id}, after={"u": user_id, "ok": True})

        await asyncio.gather(*(update(f"u-{i}") for i in range(5)))

        recorded = {row.target: json.loads(row.after_value)["u"] for row in self.rows}
        self.assertEqual(recorded, {f"user:u-{i}": f"u-{i}" for i in range(5)})


class SerializerTest(unittest.TestCase):
    def test_none_stays_none(self) -> None:
        """None 与空字符串要能区分（「无前值」 vs 「前值是空串」）。

        入库后分别是 NULL 与空串，审计查询里一眼能分辨。
        """
        self.assertIsNone(writer.serialize_value(None))
        self.assertEqual(writer.serialize_value(""), "")

    def test_text_is_not_escaping_double_encoded(self) -> None:
        self.assertEqual(writer.serialize_value("原样文本"), "原样文本")

    def test_dict_serialized_as_chinese_json(self) -> None:
        self.assertEqual(writer.serialize_value({"角色": "admin"}), '{"角色": "admin"}')

    def test_oversized_value_truncated(self) -> None:
        value = writer.serialize_value("x" * (writer.MAX_VALUE_CHARS + 100))
        self.assertLess(len(value), writer.MAX_VALUE_CHARS + 100)
        self.assertIn("已截断", value)

    def test_non_serializable_falls_back_to_repr(self) -> None:
        """自定义对象不丢记录：退化成 repr（json.dumps 的 default=str 兜住）。"""
        class Weird:
            def __repr__(self) -> str:
                return "<weird>"

        self.assertEqual(json.loads(writer.serialize_value(Weird())), "<weird>")


class WriteFailureTest(AuditTestCase):
    async def test_write_failure_does_not_raise(self) -> None:
        """审计表不可写时业务照常——但必须留 ERROR 日志（见 writer 模块说明）。"""

        @audit(AuditAction.LOGIN, target="username:{username}")
        async def login(username: str) -> str:
            return "ok"

        from contextlib import asynccontextmanager
        from unittest.mock import patch as _patch

        @asynccontextmanager
        async def _broken_scope():
            raise RuntimeError("数据库不可用")
            yield  # pragma: no cover

        with _patch("app.services.audit.writer.session_scope", _broken_scope):
            self.assertEqual(await login("someone"), "ok")

        self.assertEqual(self.rows, [])      # 没写进去，但业务成功了

    async def test_write_entry_reports_success(self) -> None:
        ok = await write_entry(AuditAction.LOGIN, target="username:someone")
        self.assertTrue(ok)
        self.assertEqual(len(self.rows), 1)


class ActionContractTest(unittest.TestCase):
    """动作取值与 DDL 注释一致（改一处必须改另一处）。"""

    def test_actions_match_ddl_comment(self) -> None:
        from pathlib import Path

        ddl = Path(__file__).resolve().parents[1] / "sql" / "business" / "01_business_schema.sql"
        content = ddl.read_text(encoding="utf-8")
        action_line = next(line for line in content.splitlines()
                           if "`action` varchar(64)" in line)

        for action in AuditAction:
            with self.subTest(action=action.value):
                self.assertIn(action.value, action_line)

    def test_every_action_has_chinese_label(self) -> None:
        for action in AuditAction:
            with self.subTest(action=action.value):
                self.assertTrue(action.label)


if __name__ == "__main__":
    unittest.main()
