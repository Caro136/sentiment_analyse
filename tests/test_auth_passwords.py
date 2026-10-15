"""口令哈希测试。

覆盖两类「不报错但结果不对」的情况：
  1. 哈希串损坏时应当**校验失败**而不是抛异常（否则一条脏数据能让接口 500）；
  2. 长口令必须完整参与哈希——bcrypt 的 72 字节静默截断正是本模块弃用它的原因，
     测试钉死这个性质（120 字符的口令前半段相同、后半段不同，必须判为不同）。
"""

import unittest

from app.services.auth import passwords


class HashAndVerifyTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        hashed = passwords.hash_password("Passw0rd1")
        self.assertTrue(passwords.verify_password("Passw0rd1", hashed))

    def test_wrong_password_rejected(self) -> None:
        hashed = passwords.hash_password("Passw0rd1")
        self.assertFalse(passwords.verify_password("passw0rd1", hashed))
        self.assertFalse(passwords.verify_password("", hashed))

    def test_same_password_different_hash(self) -> None:
        """加盐：同一口令两次哈希结果必须不同，否则可被彩虹表命中。"""
        first = passwords.hash_password("Passw0rd1")
        second = passwords.hash_password("Passw0rd1")
        self.assertNotEqual(first, second)
        self.assertTrue(passwords.verify_password("Passw0rd1", first))
        self.assertTrue(passwords.verify_password("Passw0rd1", second))

    def test_hash_is_self_describing(self) -> None:
        """参数随哈希串存储，将来调强参数时旧口令仍可校验。"""
        hashed = passwords.hash_password("Passw0rd1")
        scheme, n, r, p, salt, digest = hashed.split("$")
        self.assertEqual(scheme, "scrypt")
        self.assertEqual(int(n), passwords.PARAMS["n"])
        self.assertEqual(int(r), passwords.PARAMS["r"])
        self.assertEqual(int(p), passwords.PARAMS["p"])
        self.assertTrue(salt and digest)

    def test_plaintext_never_appears_in_hash(self) -> None:
        hashed = passwords.hash_password("Passw0rd1")
        self.assertNotIn("Passw0rd1", hashed)

    def test_long_password_not_truncated(self) -> None:
        """120 字符的口令前 72 字节相同、之后不同 —— 不得被判为同一个口令。"""
        common = "A1" * 36                        # 72 字节
        first = common + "a" * 48
        second = common + "b" * 48
        hashed = passwords.hash_password(first)
        self.assertTrue(passwords.verify_password(first, hashed))
        self.assertFalse(passwords.verify_password(second, hashed))


class BrokenHashTest(unittest.TestCase):
    """脏哈希串的处理：一律判为失败，绝不抛异常。"""

    def test_malformed_hashes_fail_closed(self) -> None:
        broken = [
            "",
            "not-a-hash",
            "bcrypt$12$abcdef",                     # 前缀不是 scrypt
            "scrypt$16384$8$1$onlyfiveparts",       # 字段数不足
            "scrypt$abc$8$1$c2FsdA==$aGFzaA==",     # 参数非数字
            "scrypt$16384$8$1$!!!notbase64!!!$aGFzaA==",
        ]
        for value in broken:
            with self.subTest(hashed=value):
                self.assertFalse(passwords.verify_password("Passw0rd1", value))

    def test_illegal_params_fail_closed(self) -> None:
        """参数被篡改成非法值（n 不是 2 的幂）时按失败处理，而不是冒泡 ValueError。"""
        hashed = passwords.hash_password("Passw0rd1")
        scheme, _, r, p, salt, digest = hashed.split("$")
        tampered = f"{scheme}$15${r}${p}${salt}${digest}"
        self.assertFalse(passwords.verify_password("Passw0rd1", tampered))


class RehashTest(unittest.TestCase):
    def test_current_params_need_no_rehash(self) -> None:
        self.assertFalse(passwords.needs_rehash(passwords.hash_password("Passw0rd1")))

    def test_weaker_params_need_rehash(self) -> None:
        hashed = passwords.hash_password("Passw0rd1")
        scheme, _, r, p, salt, digest = hashed.split("$")
        self.assertTrue(passwords.needs_rehash(f"{scheme}$1024${r}${p}${salt}${digest}"))

    def test_broken_hash_needs_rehash(self) -> None:
        self.assertTrue(passwords.needs_rehash("garbage"))


class FingerprintTest(unittest.TestCase):
    def test_fingerprint_changes_with_password(self) -> None:
        first = passwords.password_fingerprint(passwords.hash_password("Passw0rd1"))
        second = passwords.password_fingerprint(passwords.hash_password("Passw0rd2"))
        self.assertNotEqual(first, second)

    def test_fingerprint_is_short_and_stable(self) -> None:
        hashed = passwords.hash_password("Passw0rd1")
        self.assertEqual(passwords.password_fingerprint(hashed),
                         passwords.password_fingerprint(hashed))
        self.assertEqual(len(passwords.password_fingerprint(hashed)), 16)

    def test_fingerprint_matches_tokens_inline_implementation(self) -> None:
        """tokens 模块内联了同一算法（为了零项目内依赖），两处必须一致。"""
        from app.services.auth.tokens import _fingerprint

        hashed = passwords.hash_password("Passw0rd1")
        self.assertEqual(passwords.password_fingerprint(hashed), _fingerprint(hashed))


class PasswordPolicyTest(unittest.TestCase):
    def test_acceptable_passwords(self) -> None:
        for value in ["Passw0rd1", "Admin@123", "a1234567", "Pr@12345"]:
            with self.subTest(password=value):
                self.assertEqual(passwords.password_problems(value), [])

    def test_too_short(self) -> None:
        problems = passwords.password_problems("Ab1")
        self.assertTrue(any("长度" in p for p in problems))

    def test_letters_and_digits_required(self) -> None:
        self.assertTrue(passwords.password_problems("12345678"))
        self.assertTrue(passwords.password_problems("abcdefgh"))

    def test_too_long(self) -> None:
        problems = passwords.password_problems("A1" + "x" * 200)
        self.assertTrue(any("不超过" in p for p in problems))

    def test_username_as_password_rejected(self) -> None:
        problems = passwords.password_problems("admin123", username="admin123")
        self.assertTrue(any("用户名" in p for p in problems))

    def test_empty_password(self) -> None:
        self.assertTrue(passwords.password_problems(""))


class DummyHashTest(unittest.TestCase):
    """用户不存在时也要走一次等价开销的校验，DummyHash 必须可解析。"""

    def test_dummy_hash_is_wellformed(self) -> None:
        """替身哈希必须能被正常解析，否则等开销校验会退化成"立刻返回"。

        它对应某个固定口令（就是它自己），这不构成风险：authenticate() 在
        用户不存在时无条件抛 AUTH_004，校验结果被丢弃——这里只保证开销等价。
        """
        self.assertIsNotNone(passwords._parse(passwords.DUMMY_HASH))
        self.assertFalse(passwords.needs_rehash(passwords.DUMMY_HASH))
        self.assertFalse(passwords.verify_password("Passw0rd1", passwords.DUMMY_HASH))


if __name__ == "__main__":
    unittest.main()
