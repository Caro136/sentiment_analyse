"""会话票据测试。

票据是无状态签名的，因此「哪些情况必须判为不可用」就是它的全部安全性：
伪造、篡改、过期、换密钥、口令指纹不匹配。这几种情况的区分（AUTH_001 与 AUTH_002）
直接影响前端行为（跳登录 vs 提示），所以逐条钉死。
"""

import unittest
from unittest.mock import patch

from app.services.auth import passwords, tokens
from engines.contracts.config import Settings


def _settings(**overrides) -> Settings:
    """构造配置对象。默认带密钥——临时密钥路径单独测。"""
    base = {"session_secret": "unit-test-secret", "session_max_age_seconds": 3600}
    base.update(overrides)
    return Settings(**base)


class SessionRoundtripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = _settings()
        self.password_hash = passwords.hash_password("Passw0rd1")

    def test_issue_and_read(self) -> None:
        token = tokens.issue_session("u1", self.password_hash, settings=self.settings)
        payload = tokens.read_session(token, settings=self.settings)
        self.assertEqual(payload.user_id, "u1")
        self.assertEqual(payload.fingerprint,
                         passwords.password_fingerprint(self.password_hash))

    def test_payload_does_not_carry_role(self) -> None:
        """票据里不放角色：角色每次回查数据库，改角色才能立即生效。

        这条断言是防回归的——往里加 role 会让「降级管理员却仍是管理员」重新出现。
        """
        token = tokens.issue_session("u1", self.password_hash, settings=self.settings)
        data = tokens._serializer(self.settings).loads(token)
        self.assertEqual(set(data.keys()), {"uid", "pwd"})


class SessionFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = _settings()
        self.password_hash = passwords.hash_password("Passw0rd1")
        self.token = tokens.issue_session("u1", self.password_hash, settings=self.settings)

    def test_missing_token(self) -> None:
        with self.assertRaises(tokens.SessionInvalid):
            tokens.read_session("", settings=self.settings)

    def test_tampered_token(self) -> None:
        tampered = self.token[:-4] + ("aaaa" if not self.token.endswith("aaaa") else "bbbb")
        with self.assertRaises(tokens.SessionInvalid):
            tokens.read_session(tampered, settings=self.settings)

    def test_other_secret_rejected(self) -> None:
        """换密钥后旧票据必须失效（密钥泄露的处置手段就是换密钥）。"""
        with self.assertRaises(tokens.SessionInvalid):
            tokens.read_session(self.token, settings=_settings(session_secret="another"))

    def test_expired_token(self) -> None:
        with self.assertRaises(tokens.SessionExpired):
            tokens.read_session(self.token, settings=self.settings, max_age=-1)

    def test_expiry_uses_configured_max_age(self) -> None:
        short = _settings(session_max_age_seconds=-1)
        with self.assertRaises(tokens.SessionExpired):
            tokens.read_session(self.token, settings=short)

    def test_garbage_payload_rejected(self) -> None:
        """签名有效但载荷不是预期结构时也算非法。"""
        token = tokens._serializer(self.settings).dumps(["not", "a", "dict"])
        with self.assertRaises(tokens.SessionInvalid):
            tokens.read_session(token, settings=self.settings)

        token = tokens._serializer(self.settings).dumps({"pwd": "x"})
        with self.assertRaises(tokens.SessionInvalid):
            tokens.read_session(token, settings=self.settings)

    def test_password_change_invalidates_fingerprint(self) -> None:
        """重置口令后，票据仍在有效期内但指纹对不上 —— 由 service 判为会话失效。"""
        payload = tokens.read_session(self.token, settings=self.settings)
        new_hash = passwords.hash_password("Passw0rd2")
        self.assertNotEqual(payload.fingerprint, passwords.password_fingerprint(new_hash))


class SecretSourceTest(unittest.TestCase):
    def test_ephemeral_secret_when_unset(self) -> None:
        settings = _settings(session_secret="")
        self.assertTrue(tokens.ephemeral_secret_active(settings))

        token = tokens.issue_session("u1", "hash", settings=settings)
        self.assertEqual(tokens.read_session(token, settings=settings).user_id, "u1")

    def test_configured_secret_is_not_ephemeral(self) -> None:
        self.assertFalse(tokens.ephemeral_secret_active(_settings()))


class CookieTest(unittest.TestCase):
    def test_cookie_flags(self) -> None:
        from starlette.responses import JSONResponse

        settings = _settings(session_cookie_secure=False)
        response = JSONResponse(content={})
        tokens.set_session_cookie(response, "token-value", settings=settings)

        header = response.headers["set-cookie"]
        self.assertIn(f"{settings.session_cookie_name}=token-value", header)
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=lax", header.lower().replace("samesite=lax", "SameSite=lax"))
        self.assertIn("Path=/", header)
        self.assertNotIn("Secure", header)      # http 演示环境不能带 Secure

    def test_secure_flag_follows_config(self) -> None:
        from starlette.responses import JSONResponse

        response = JSONResponse(content={})
        tokens.set_session_cookie(response, "t", settings=_settings(session_cookie_secure=True))
        self.assertIn("Secure", response.headers["set-cookie"])

    def test_clear_cookie_removes_value(self) -> None:
        from starlette.responses import JSONResponse

        response = JSONResponse(content={})
        tokens.clear_session_cookie(response, settings=_settings())
        header = response.headers["set-cookie"]
        self.assertIn("sentiment_session=", header)
        self.assertIn("expires", header.lower())


class SerializerDigestTest(unittest.TestCase):
    def test_uses_sha256_signer(self) -> None:
        """显式指定 SHA-256：itsdangerous 默认是 SHA-1。"""
        settings = _settings()
        with patch.object(tokens, "_secret", return_value=settings.session_secret):
            serializer = tokens._serializer(settings)
        digest_method = serializer.signer_kwargs.get("digest_method")
        self.assertEqual(digest_method().name, "sha256")


if __name__ == "__main__":
    unittest.main()
