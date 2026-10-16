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


class FakeHost:
    """主持人替身：只回答「完成没有、产物在哪」，不真的研判。"""

    def __init__(self, *, complete: bool, report_path: str = "",
                 running: bool = True, judged: tuple[str, ...] = ()) -> None:
        from engines.host_agent.agent import HostStatus

        self._status = HostStatus(running=running, complete=complete,
                                  report_path=report_path, judged=list(judged))

    def start(self, query: str) -> None:  # noqa: ARG002 —— 替身忽略参数
        return None

    async def stop(self) -> None:
        return None

    def status(self):
        return self._status


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
        # 等主持人收尾的超时压到 0.2s：这些用例里没人发章节事件，
        # 主持人永远不会 complete，用默认的 900s 会让测试挂住。
        # 同时装一个「已完成」的主持人替身——本类关注角色行为，
        # 「主持人还没完成」的情形由 HostWaitTest 单独覆盖。
        orch = ResearchOrchestrator(host_timeout=0.2)
        orch._host = FakeHost(complete=True, report_path="/tmp/host.md")
        return orch

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


class SequencedHost(FakeHost):
    """前 `pending_calls` 次 `status()` 报「未完成」，之后报「已完成」。

    用来验证「编排层确实在等主持人」这件事本身——静态替身证明不了等待。
    """

    def __init__(self, *, pending_calls: int, report_path: str = "/tmp/host.md") -> None:
        super().__init__(complete=False, report_path=report_path)
        self._remaining = pending_calls
        self._report_path = report_path

    def status(self):
        from engines.host_agent.agent import HostStatus

        if self._remaining > 0:
            self._remaining -= 1
            return HostStatus(running=True, complete=False,
                              judged=["background_overview"])
        return HostStatus(running=True, complete=True, report_path=self._report_path)


class HostWaitTest(unittest.TestCase):
    """角色跑完 ≠ 研判完成：主持人还在研判时不能报 completed。

    这条约束是 T-34 端到端联调逼出来的：编排层原先在**两个角色返回**时就把任务置为
    completed，调用方据此立刻去生成报告——而主持人还没写完最终裁判，
    报告引擎找不到 `host_*.md`，只能以「产物尚未生成」失败，且看不出是为什么。
    """

    def setUp(self) -> None:
        self._original = dict(get_registry())

    def tearDown(self) -> None:
        registry = get_registry()
        registry.clear()
        registry.update(self._original)

    def _install(self, host: FakeHost) -> ResearchOrchestrator:
        registry = get_registry()

        def make(key: str):
            async def bound(query: str):  # noqa: ARG001
                return FakeResult(ok=True)
            return bound

        for key in ("insight", "media"):
            registry[key] = ResearchRole(key, registry[key].display_name, make(key))
        # 等待上限同样压小：验证的是「会等、等不到会如实标失败」，不是等多久
        orch = ResearchOrchestrator(host_timeout=0.2)
        orch._host = host
        return orch

    def _run(self, orch: ResearchOrchestrator) -> ResearchTask:
        from unittest.mock import patch

        async def run():
            task = await orch.start("测试主题")
            for _ in range(200):
                if task.status != STATUS_RUNNING:
                    break
                await asyncio.sleep(0.01)
            await orch.stop()
            return task

        # 轮询间隔压到 10ms：验证的是「会等」，不是「等多久」
        with patch("engines.orchestration.research.HOST_POLL_SECONDS", 0.01):
            return asyncio.run(run())

    def test_waits_for_host_then_completes(self) -> None:
        task = self._run(self._install(SequencedHost(pending_calls=3)))

        self.assertEqual(task.status, STATUS_COMPLETED)
        self.assertEqual(task.host_path, "/tmp/host.md")
        self.assertEqual(task.error, "")

    def test_incomplete_host_marks_task_failed(self) -> None:
        """主持人没写完就不能算完成——宁可如实标失败，也不给一个「假完成」。"""
        task = self._run(self._install(FakeHost(complete=False)))

        self.assertEqual(task.status, STATUS_FAILED)
        self.assertIn("主持人", task.error)
        self.assertEqual(task.host_path, "")
        self.assertIn("主持人", task.render())

    def test_host_stopped_returns_immediately(self) -> None:
        """主持人已停（running=False）时立即返回，不空等满超时。"""
        task = self._run(self._install(FakeHost(complete=False, running=False)))

        self.assertEqual(task.status, STATUS_FAILED)


if __name__ == "__main__":
    unittest.main()
