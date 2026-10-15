"""LLM 客户端测试。

只测**不需要真实调用模型**的部分：供应商识别、未配置时的行为、
返回结构兼容、并发限制。真实调用属于集成测试，需要密钥，不放在单元测试里。
"""

import asyncio
import unittest

from engines.common.llm_client import (
    LLMClient,
    LLMNotConfiguredError,
    detect_provider,
    gather_limited,
)
from engines.contracts.config import LLMRoleSettings


class _FakeResponse:
    """模拟模型返回对象。"""

    def __init__(self, content):
        self.content = content


class ProviderDetectionTest(unittest.TestCase):
    def test_moonshot(self) -> None:
        self.assertEqual(
            detect_provider("https://api.moonshot.cn/v1", "kimi-k2-0711-preview"),
            "moonshot")

    def test_deepseek(self) -> None:
        self.assertEqual(
            detect_provider("https://api.deepseek.com/v1", "deepseek-chat"),
            "deepseek")

    def test_dashscope(self) -> None:
        self.assertEqual(
            detect_provider("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-max"),
            "dashscope")

    def test_fallback_for_unknown_endpoint(self) -> None:
        self.assertEqual(
            detect_provider("https://some-proxy.example.com/v1", "some-model"),
            "openai_compatible")


class LLMClientTest(unittest.TestCase):
    def test_unconfigured_client_reports_false(self) -> None:
        client = LLMClient("insight", LLMRoleSettings())
        self.assertFalse(client.configured)
        self.assertEqual(client.model_name, "(未配置)")

    def test_unconfigured_call_raises_actionable_error(self) -> None:
        """未配置时报错必须能指导修复，而不是抛出难以定位的底层异常。"""
        client = LLMClient("insight", LLMRoleSettings())
        with self.assertRaises(LLMNotConfiguredError) as ctx:
            asyncio.run(client.generate_text("你好"))
        self.assertIn("INSIGHT_API_KEY", str(ctx.exception))

    def test_configured_flag(self) -> None:
        client = LLMClient("media", LLMRoleSettings(api_key="k", model_name="m"))
        self.assertTrue(client.configured)

    def test_repr_shows_state(self) -> None:
        client = LLMClient("host", LLMRoleSettings())
        self.assertIn("unconfigured", repr(client))
        self.assertIn("host", repr(client))

    def test_content_of_handles_string(self) -> None:
        self.assertEqual(LLMClient._content_of("abc"), "abc")
        self.assertEqual(LLMClient._content_of(_FakeResponse("xy")), "xy")

    def test_content_of_handles_block_list(self) -> None:
        """不同版本返回的内容块结构不同，必须都能取到文本。"""
        response = _FakeResponse([{"text": "a"}, {"text": "b"}])
        self.assertEqual(LLMClient._content_of(response), "ab")

    def test_build_messages_without_system(self) -> None:
        messages = LLMClient._build_messages("提问", None)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")

    def test_build_messages_with_system(self) -> None:
        messages = LLMClient._build_messages("提问", "你是分析师")
        self.assertEqual([m["role"] for m in messages], ["system", "user"])


class GatherLimitedTest(unittest.TestCase):
    def test_respects_concurrency_limit(self) -> None:
        """打标场景会批量调用模型，不限并发会直接触发限流。"""
        state = {"running": 0, "peak": 0}

        async def job(index: int) -> int:
            state["running"] += 1
            state["peak"] = max(state["peak"], state["running"])
            await asyncio.sleep(0.01)
            state["running"] -= 1
            return index

        results = asyncio.run(gather_limited([job(i) for i in range(12)], limit=3))

        self.assertEqual(len(results), 12)
        self.assertEqual(results, list(range(12)))
        self.assertLessEqual(state["peak"], 3, "实际并发超过了限制")

    def test_exceptions_are_returned_not_raised(self) -> None:
        """单条失败不应中断整批——批量打标里一条内容失败不该丢掉其余结果。"""
        async def good() -> int:
            return 1

        async def bad() -> int:
            raise ValueError("单条失败")

        results = asyncio.run(gather_limited([good(), bad()], limit=2))

        self.assertEqual(results[0], 1)
        self.assertIsInstance(results[1], ValueError)

    def test_zero_limit_is_normalized(self) -> None:
        async def job() -> str:
            return "ok"

        results = asyncio.run(gather_limited([job()], limit=0))
        self.assertEqual(results, ["ok"])


if __name__ == "__main__":
    unittest.main()
