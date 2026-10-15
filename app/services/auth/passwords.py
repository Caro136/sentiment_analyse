"""口令哈希：`hashlib.scrypt`（标准库）。

**为什么不用 passlib/bcrypt**（设计规格 8.1 原本的首选）：
本机实测 passlib 1.7.4 与 bcrypt 5.0 完全不兼容——passlib 读 `bcrypt.__about__.__version__`
（bcrypt 4.1 起已移除）而失败，且 bcrypt 5.0 对超过 72 字节的口令直接抛
`ValueError: password cannot be longer than 72 bytes`，连 `hash('admin123')` 都跑不通。
scrypt 是设计规格 8.1 明确允许的备选，且：① 标准库自带，不引入第三方依赖；
② 无 72 字节长度上限（长口令不会被静默截断）；③ 内存硬化参数可随算力上调。

哈希串是自描述的：`scrypt$n$r$p$<salt_b64>$<hash_b64>`。
参数随串存储，因此将来调强参数时旧口令仍可校验，登录时按 `needs_rehash()` 静默升级。

本模块**不依赖任何项目内模块**，可单独测试与复用。
"""

import base64
import hashlib
import hmac
import secrets

__all__ = [
    "SCHEME",
    "PARAMS",
    "MIN_PASSWORD_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "hash_password",
    "verify_password",
    "needs_rehash",
    "password_fingerprint",
    "password_problems",
    "DUMMY_HASH",
]

SCHEME = "scrypt"

# n=2^14（内存 16MB）、r=8、p=1 约 30~50ms/次：单次登录可接受，
# 暴力破解成本足够高。调大只需改这里——旧哈希因自带参数仍可校验。
PARAMS: dict[str, int] = {"n": 2 ** 14, "r": 8, "p": 1}
KEY_LENGTH = 32
SALT_BYTES = 16

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _derive(password: str, salt: bytes, params: dict[str, int]) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=params["n"],
        r=params["r"],
        p=params["p"],
        dklen=KEY_LENGTH,
    )


def hash_password(password: str) -> str:
    """生成自描述的口令哈希串。"""
    salt = secrets.token_bytes(SALT_BYTES)
    return _pack(salt, _derive(password, salt, PARAMS))


def _pack(salt: bytes, digest: bytes) -> str:
    return (f"{SCHEME}${PARAMS['n']}${PARAMS['r']}${PARAMS['p']}"
            f"${_b64encode(salt)}${_b64encode(digest)}")


# 登录时用户名不存在也要走一次等价开销的校验，否则「响应快 = 该用户不存在」
# 会成为账号枚举的侧信道。固定盐 + 固定口令，模块导入时算一次（约 40ms）。
DUMMY_HASH = _pack(b"0" * SALT_BYTES, _derive("dummy-password", b"0" * SALT_BYTES, PARAMS))


def _parse(hashed: str) -> tuple[dict[str, int], bytes, bytes] | None:
    """解析哈希串；格式不符返回 None（调用方按校验失败处理）。

    哈希串损坏时**返回 False 而不是抛异常**：`password_hash` 字段若是脏数据，
    登录该失败就失败，不该让整个接口 500。
    """
    try:
        scheme, n, r, p, salt_b64, hash_b64 = hashed.split("$")
        if scheme != SCHEME:
            return None
        params = {"n": int(n), "r": int(r), "p": int(p)}
        return params, _b64decode(salt_b64), _b64decode(hash_b64)
    except (ValueError, TypeError, base64.binascii.Error):
        return None


def verify_password(password: str, hashed: str) -> bool:
    """校验口令。恒定时间比较，避免按比较耗时推断哈希。"""
    parsed = _parse(hashed or "")
    if parsed is None:
        return False
    params, salt, expected = parsed
    try:
        actual = _derive(password, salt, params)
    except (ValueError, MemoryError):
        # 参数被篡改成非法值（如 n 非 2 的幂）时按失败处理
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(hashed: str) -> bool:
    """哈希是否使用了过时的参数（登录成功后据此静默升级）。"""
    parsed = _parse(hashed or "")
    if parsed is None:
        return True
    params, _, _ = parsed
    return params != PARAMS


def password_fingerprint(hashed: str) -> str:
    """口令哈希的短指纹，写进会话票据。

    作用是让「重置口令/改口令」能立刻踢掉该用户的所有既有会话：
    票据里带着签发时的指纹，请求时与库中当前哈希比对，不一致即会话失效。
    这是无服务端会话表时**唯一**真正可撤销的会话入口（见 tokens 模块说明）。
    """
    return hashlib.sha256((hashed or "").encode("utf-8")).hexdigest()[:16]


def password_problems(password: str, *, username: str = "") -> list[str]:
    """口令强度检查，返回问题清单（空列表表示合格）。

    只做「长度 + 字符类别」这类可解释的硬性检查，不做黑名单——
    面向的是内部演示与毕设场景，策略过复杂反而会让演示账号被自己拦住。
    """
    problems: list[str] = []
    password = password or ""

    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"长度至少 {MIN_PASSWORD_LENGTH} 位")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"长度不超过 {MAX_PASSWORD_LENGTH} 位")
    if not any(ch.isalpha() for ch in password):
        problems.append("需包含字母")
    if not any(ch.isdigit() for ch in password):
        problems.append("需包含数字")
    if username and password.lower() == username.lower():
        problems.append("不得与用户名相同")

    return problems
