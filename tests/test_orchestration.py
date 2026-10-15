"""研判编排测试。

角色入口用替身驱动：验证注册表解析、任务状态流转、并发互斥，
不真正跑两个研究员（那是分钟级的集成验证）。
"""

import asyncio
import unittest

from engines.orchestration.registry import (
    ResearchRole,
    get_registry,
    resolve_roles,
    role_names,
)
from engines.orchestration.research import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    ResearchOrchestrator,
    ResearchTask,
    RoleOutcome,
)


class FakeResult:
    """角色返回值的替身。"""

    def __init__(self, ok: bool = True, path: str = "/tmp/report.md",
                 error: str = "") -> None:
        self.ok = ok
        self.report_path = path if ok else ""
        self.error = error

    def render(self) -> str:
        return "角色完成：5 章" if self.ok else f"失败：{self.error}"


class RegistryTest(unittest.TestCase):
    def test_two_roles_registered(self) -> None:
        self.assertEqual(role_names(), ["insight", "media"])

    def test_resolve_all_by_default(self) -> None:
        self.assertEqual(len(resolve_roles()), 2)

    def test_resolve_subset(self) -> None:
        roles = resolve_roles(["insight"])
        self.assertEqual([r.key for r in roles], ["insight"])

    def test_unknown_role_raises(self) -> None:
        """未知角色直接报错而不是静默跳过——否则任务"成功"但少跑了一个角色。"""
        with self.assertRaises(KeyError):
            resolve_roles(["not_a_role"])

    def test_role_carries_report_dir(self) -> None:
        role = get_registry()["insight"]
        self.assertEqual(role.report_dir_attr, "insight_report_dir")
        self.assertTrue(role.display_name)

    def test_invoke_is_callable(self) -> None:
        for role in resolve_roles():
            self.assertTrue(callable(role.invoke))


class TaskStateTest(unittest.TestCase):
    def _task(self) -> ResearchTask:
        return ResearchTask(task_id="t1", query="测试主题")

    def test_initial_state(self) -> None:
        task = self._task()
        self.assertEqual(task.status, STATUS_RUNNING)
        self.assertEqual(task.succeeded, 0)
        self.assertEqual(task.failed_roles, [])

    def test_succeeded_count(self) -> None:
        task = self._task()
        task.outcomes["insight"] = RoleOutcome("insight", "私域研究员", ok=True)
        task.outcomes["media"] = RoleOutcome("media", "公域研究员", ok=False, error="超时")
        self.assertEqual(task.succeeded, 1)
        self.assertEqual(task.failed_roles, ["media"])

    def test_render_shows_each_role(self) -> None:
        task = self._task()
        task.outcomes["insight"] = RoleOutcome("insight", "私域研究员", ok=True,
                                               summary="5 章完成")
        task.outcomes["media"] = RoleOutcome("media", "公域研究员", ok=False,
                                             error="搜索不可用")
        text = task.render()
        self.assertIn("私域研究员", text)
        self.assertIn("公域研究员", text)
        self.assertIn("搜索不可用", text)

    def test_to_dict_shape(self) -> None:
        task = self._task()
        task.outcomes["insight"] = RoleOutcome("insight", "私域研究员", ok=True,
                                               duration=12.34)
        payload = task.to_dict()
        self.assertEqual(payload["task_id"], "t1")
        self.assertEqual(payload["query"], "测试主题")
        self.assertIn("insight", payload["results"])
        self.assertEqual(payload["results"]["insight"]["duration"], 12.3)


class OrchestratorTest(unittest.TestCase):
    """用替身角色驱动编排器，验证任务状态流转与并发互斥。"""
    def setUp(self) -> None:
        self._original = dict(get_registry())

    def tearDown(self) -> None:
        registry = get_registry()
        registry.clear()
        registry.update(self._original)

    def _install(self, behaviour: dict[str, object]) -> ResearchOrchestrator:
        registry = get_registry()

        def make(key: str):
            async def bound(query: str):
                outcome = behaviour[key]
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            return bound

        for key in behaviour:
            registry[key] = ResearchRole(key, registry[key].display_name, make(key))
        return ResearchOrchestrator()

    def test_task_completes_when_all_roles_succeed(self) -> None:
        orch = self._install({
            "insight": FakeResult(ok=True, path="/tmp/i.md"),
            "media": FakeResult(ok=True, path="/tmp/m.md"),
        })

        async def run():
            task = await orch.start("测试主题")
            for _ in range(100):
                if task.status != STATUS_RUNNING:
                    break
                await asyncio.sleep(0.02)
            await orch.stop()
            return task

        task = asyncio.run(run())
        self.assertEqual(task.status, STATUS_COMPLETED)
        self.assertEqual(task.succeeded, 2)
        self.assertEqual(task.outcomes["insight"].report_path, "/tmp/i.md")

    def test_role_exception_recorded_not_raised(self) -> None:
        """单个角色抛异常不该让整个任务崩溃，要记进结果并发布错误事件。"""
        orch = self._install({
            "insight": FakeResult(ok=True),
            "media": RuntimeError("搜索服务不可用"),
        })

        async def run():
            task = await orch.start("测试主题")
            for _ in range(100):
                if task.status != STATUS_RUNNING:
                    break
                await asyncio.sleep(0.02)
            await orch.stop()
            return task

        task = asyncio.run(run())
        self.assertEqual(task.status, STATUS_FAILED)
        self.assertEqual(task.succeeded, 1)
        self.assertIn("media", task.failed_roles)
        self.assertIn("搜索服务不可用", task.outcomes["media"].error)

    def test_role_reporting_failure_marked_not_ok(self) -> None:
        """角色正常返回但自身判定失败（ok=False）也要如实记录。"""
        orch = self._install({
            "insight": FakeResult(ok=False, error="没有召回任何材料"),
            "media": FakeResult(ok=True),
        })

        async def run():
            task = await orch.start("测试主题")
            for _ in range(100):
                if task.status != STATUS_RUNNING:
                    break
                await asyncio.sleep(0.02)
            await orch.stop()
            return task

        task = asyncio.run(run())
        self.assertEqual(task.status, STATUS_FAILED)
        self.assertIn("没有召回任何材料", task.outcomes["insight"].error)

    def test_concurrent_task_rejected(self) -> None:
        """两个主题并发跑会让同名维度被错误配对，必须拒绝。"""
        orch = self._install({
            "insight": FakeResult(), "media": FakeResult(),
        })

        async def run():
            first = await orch.start("第一个主题")
            with self.assertRaises(RuntimeError):
                await orch.start("第二个主题")
            await orch.stop()
            return first

        task = asyncio.run(run())
        self.assertIsNotNone(task)

    def test_stop_ends_running_task(self) -> None:
        orch = self._install({
            "insight": FakeResult(), "media": FakeResult(),
        })

        async def run():
            task = await orch.start("测试主题")
            await orch.stop()
            return task

        task = asyncio.run(run())
        self.assertEqual(task.status, STATUS_FAILED)
        self.assertIsNone(orch.current)

    def test_latest_task_lookup(self) -> None:
        orch = self._install({"insight": FakeResult(), "media": FakeResult()})

        async def run():
            task = await orch.start("测试主题")
            await orch.stop()
            return task

        task = asyncio.run(run())
        self.assertIs(orch.get_task(task.task_id), task)
        self.assertIs(orch.latest_task(), task)

    def test_clear_finished_keeps_current(self) -> None:
        orch = self._install({"insight": FakeResult(), "media": FakeResult()})

        async def run():
            await orch.start("测试主题")
            await orch.stop()
            return orch.clear_finished()

        removed = asyncio.run(run())
        self.assertEqual(removed, 1)


if __name__ == "__main__":
    unittest.main()
