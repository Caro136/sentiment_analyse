"""重试器测试。

测试统一使用零延迟配置——否则每条重试用例都要真等几秒。
"""

import asyncio
import unittest

from engines.common.runtime.call_retry import (
    RetryConfig,
    delay_for,
    is_retryable,
    with_graceful_retry,
    with_retry,
)

FAST = RetryConfig(max_retries=3, initial_delay=0.0, backoff_factor=1.0,
                   max_delay=0.0, jitter=0.0)


class DelayTest(unittest.TestCase):
    def test_exponential_growth_and_cap(self) -> None:
        cfg = RetryConfig(initial_delay=2.0, backoff_factor=2.0,
                          max_delay=10.0, jitter=0.0)
        self.assertEqual(delay_for(1, cfg), 2.0)
        self.assertEqual(delay_for(2, cfg), 4.0)
        self.assertEqual(delay_for(3, cfg), 8.0)
        self.assertEqual(delay_for(4, cfg), 10.0)   # 封顶
        self.assertEqual(delay_for(20, cfg), 10.0)

    def test_jitter_stays_within_bounds(self) -> None:
        cfg = RetryConfig(initial_delay=10.0, backoff_factor=1.0,
                          max_delay=10.0, jitter=0.2)
        for _ in range(50):
            delay = delay_for(1, cfg)
            self.assertGreaterEqual(delay, 8.0)
            self.assertLessEqual(delay, 12.0)


class IsRetryableTest(unittest.TestCase):
    def test_rate_limit_is_retryable(self) -> None:
        exc = Exception("too many requests")
        exc.status_code = 429
        self.assertTrue(is_retryable(exc))

    def test_server_error_is_retryable(self) -> None:
        exc = Exception("bad gateway")
        exc.status_code = 502
        self.assertTrue(is_retryable(exc))

    def test_client_error_is_not_retryable(self) -> None:
        exc = Exception("bad request")
        exc.status_code = 400
        self.assertFalse(is_retryable(exc))

    def test_auth_error_is_not_retryable(self) -> None:
        """认证失败重试一百次也是同样结果，只会白等并放大成本。"""
        class AuthenticationError(Exception):
            pass

        self.assertFalse(is_retryable(AuthenticationError("invalid api key")))

    def test_connection_error_is_retryable(self) -> None:
        self.assertTrue(is_retryable(ConnectionError("connection reset")))
        self.assertTrue(is_retryable(TimeoutError("timed out")))

    def test_unknown_exception_is_not_retryable(self) -> None:
        """真正的逻辑错误（非网络、非 HTTP）不该反复重试。"""
        self.assertFalse(is_retryable(ValueError("解析失败")))


class WithRetryTest(unittest.TestCase):
    def test_success_runs_once(self) -> None:
        calls: list = []

        @with_retry(config=FAST)
        def fn():
            calls.append(1)
            return "ok"

        self.assertEqual(fn(), "ok")
        self.assertEqual(len(calls), 1)

    def test_retries_then_succeeds(self) -> None:
        calls: list = []

        @with_retry(config=FAST)
        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("boom")
            return "ok"

        self.assertEqual(fn(), "ok")
        self.assertEqual(len(calls), 3)

    def test_exhausts_retries_and_raises(self) -> None:
        calls: list = []

        @with_retry(config=FAST)
        def fn():
            calls.append(1)
            raise ConnectionError("always fails")

        with self.assertRaises(ConnectionError):
            fn()
        self.assertEqual(len(calls), FAST.max_retries)

    def test_non_retryable_fails_fast(self) -> None:
        calls: list = []

        class AuthenticationError(Exception):
            pass

        @with_retry(config=FAST)
        def fn():
            calls.append(1)
            raise AuthenticationError("nope")

        with self.assertRaises(AuthenticationError):
            fn()
        self.assertEqual(len(calls), 1)     # 只调用一次，不重试

    def test_async_function_supported(self) -> None:
        calls: list = []

        @with_retry(config=FAST)
        async def fn():
            calls.append(1)
            if len(calls) < 2:
                raise ConnectionError("boom")
            return "ok"

        self.assertEqual(asyncio.run(fn()), "ok")
        self.assertEqual(len(calls), 2)

    def test_preserves_function_metadata(self) -> None:
        @with_retry(config=FAST)
        def my_function():
            """文档字符串。"""

        self.assertEqual(my_function.__name__, "my_function")
        self.assertEqual(my_function.__doc__, "文档字符串。")


class GracefulRetryTest(unittest.TestCase):
    def test_returns_factory_default(self) -> None:
        @with_graceful_retry(config=FAST, default_factory=list)
        def fn():
            raise ConnectionError("x")

        self.assertEqual(fn(), [])

    def test_returns_none_without_factory(self) -> None:
        @with_graceful_retry(config=FAST)
        def fn():
            raise ConnectionError("x")

        self.assertIsNone(fn())

    def test_success_passthrough(self) -> None:
        @with_graceful_retry(config=FAST, default_factory=list)
        def fn():
            return [1, 2, 3]

        self.assertEqual(fn(), [1, 2, 3])

    def test_async_graceful(self) -> None:
        @with_graceful_retry(config=FAST, default_factory=dict)
        async def fn():
            raise ConnectionError("x")

        self.assertEqual(asyncio.run(fn()), {})

    def test_default_factory_called_each_time(self) -> None:
        """兜底值必须每次新建，否则多个失败调用会共享同一个可变对象。"""
        @with_graceful_retry(config=FAST, default_factory=list)
        def fn():
            raise ConnectionError("x")

        first, second = fn(), fn()
        self.assertEqual(first, second)
        self.assertIsNot(first, second)


if __name__ == "__main__":
    unittest.main()
