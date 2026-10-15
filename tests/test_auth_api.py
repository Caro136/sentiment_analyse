"""认证接口的端到端测试（离线）。

用 `httpx2.ASGITransport` 直接驱动 FastAPI 应用，不启服务、不连 MySQL：
数据库依赖被换成 `tests/fakes.InMemoryUserRepository`。

**为什么不用 ASGI 测 SSE**（T-18 的教训）：`ASGITransport` 会等响应体读完才返回，
SSE 是永不结束的长连接，对着它发请求必然挂死。因此本文件只覆盖请求-响应式接口，
SSE 的鉴权由 `AuthCoverageTest` 用依赖链内省来验证（不需要真发请求）。

`AuthCoverageTest` 是这套测试里最值钱的一个：它遍历 OpenAPI 里全部 `/api/v1` 路径，
断言「除登录外每一条都挂着登录守卫」——后续 T-21~T-27 每加一个接口，它自动生效。
"""

import json
import unittest
from unittest.mock import patch

from httpx2 import ASGITransport, AsyncClient

from app.main import create_app
from app.security import get_user_repository
from app.services.auth import passwords
from tests.fakes import InMemoryUserRepository, capture_audit_writes, make_user

LOGIN_PATH = "/api/v1/auth/login"
PUBLIC_PATHS = {LOGIN_PATH, "/healthz", "/readyz"}


def _app_with(users):
    app = create_app()
    repo = InMemoryUserRepository(users)
    app.dependency_overrides[get_user_repository] = lambda: repo
    return app, repo


class ApiTestCase(unittest.IsolatedAsyncioTestCase):
    """基类：准备三个角色的账号，并提供登录辅助。"""

    async def asyncSetUp(self) -> None:
        # scrypt 的强度参数是为线上定的（约 40ms/次），而这里的每个用例都要
        # 建三个账号 + 若干次登录，用生产参数会让整个文件跑到 30 秒以上。
        # 只在测试期间降档：实现代码里没有任何测试开关，参数从模块属性读一次。
        patcher = patch.object(passwords, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        patcher.start()
        self.addCleanup(patcher.stop)

        # 登录与用户管理现在会自动写审计。审计走独立事务（生产是 MySQL），
        # 这里换成内存收集——否则测试会悄悄依赖本机数据库。
        self.audit_rows: list = []
        audit_patcher = capture_audit_writes(self.audit_rows)
        audit_patcher.start()
        self.addCleanup(audit_patcher.stop)

        self.admin = make_user("admin", "Admin@123", role="admin")
        self.analyst = make_user("analyst", "Analyst@123", role="analyst")
        self.pr = make_user("pr", "Pr@12345", role="pr")
        self.app, self.repo = _app_with([self.admin, self.analyst, self.pr])

        self.client = AsyncClient(transport=ASGITransport(app=self.app),
                                  base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.app.dependency_overrides.clear()

    async def login(self, username: str, password: str):
        return await self.client.post(LOGIN_PATH,
                                      json={"username": username, "password": password})


class LoginTest(ApiTestCase):
    async def test_login_success_sets_cookie_and_returns_user_info(self) -> None:
        response = await self.login("analyst", "Analyst@123")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json().keys()),
                         {"user_id", "username", "display_name", "role"})
        self.assertEqual(response.json()["role"], "analyst")

        cookie = response.headers["set-cookie"]
        self.assertIn("httponly", cookie.lower())
        self.assertNotIn("Admin@123", response.text)   # 响应体不得回显口令

    async def test_login_username_is_case_insensitive(self) -> None:
        response = await self.login("ADMIN", "Admin@123")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["username"], "admin")

    async def test_wrong_password_and_unknown_user_are_indistinguishable(self) -> None:
        """两者都必须回同一码同一文案，否则接口就成了账号枚举器。"""
        wrong = await self.login("analyst", "WrongPass1")
        missing = await self.login("nobody", "WrongPass1")

        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.json()["code"], "AUTH_004")
        self.assertEqual(missing.json()["code"], "AUTH_004")
        self.assertEqual(wrong.json()["message"], missing.json()["message"])

    async def test_disabled_account_rejected(self) -> None:
        self.repo = InMemoryUserRepository(
            [make_user("gone", "Gone@12345", role="analyst", status="disabled")])
        self.app.dependency_overrides[get_user_repository] = lambda: self.repo

        response = await self.login("gone", "Gone@12345")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "AUTH_005")

    async def test_short_username_blank_password_rejected_by_schema(self) -> None:
        response = await self.client.post(LOGIN_PATH,
                                          json={"username": "analyst", "password": ""})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")

    async def test_login_updates_last_login_at(self) -> None:
        await self.login("analyst", "Analyst@123")
        stored = await self.repo.get_by_username("analyst")
        self.assertIsNotNone(stored.last_login_at)


class SessionTest(ApiTestCase):
    async def test_me_requires_session(self) -> None:
        response = await self.client.get("/api/v1/auth/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "AUTH_001")

    async def test_me_returns_current_user(self) -> None:
        await self.login("pr", "Pr@12345")
        response = await self.client.get("/api/v1/auth/me")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "pr")

    async def test_bearer_token_also_accepted(self) -> None:
        """Cookie 是前端约定，Bearer 便于 curl 与 /docs 联调。"""
        from app.services.auth.tokens import issue_session

        token = issue_session(self.analyst.user_id, self.analyst.password_hash)
        response = await self.client.get("/api/v1/auth/me",
                                        headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], self.analyst.user_id)

    async def test_logout_clears_session(self) -> None:
        await self.login("analyst", "Analyst@123")
        logout = await self.client.post("/api/v1/auth/logout")
        self.assertEqual(logout.status_code, 204)

        after = await self.client.get("/api/v1/auth/me")
        self.assertEqual(after.status_code, 401)

    async def test_tampered_cookie_rejected(self) -> None:
        await self.login("analyst", "Analyst@123")
        self.client.cookies.set("sentiment_session", "tampered-value")
        response = await self.client.get("/api/v1/auth/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "AUTH_001")

    async def test_session_dies_when_user_disabled_mid_session(self) -> None:
        """停用要立即生效——票据还在有效期内也不行。"""
        await self.login("analyst", "Analyst@123")
        await self.repo.update(self.analyst.user_id, {"status": "disabled"})

        response = await self.client.get("/api/v1/auth/me")
        self.assertEqual(response.status_code, 401)

    async def test_session_dies_after_password_reset(self) -> None:
        """口令指纹变化让既有票据全部失效（无会话表时的撤销入口）。"""
        await self.login("analyst", "Analyst@123")
        await self.repo.update(
            self.analyst.user_id,
            {"password_hash": passwords.hash_password("Brand@New1")})

        response = await self.client.get("/api/v1/auth/me")
        self.assertEqual(response.status_code, 401)


class UserManagementTest(ApiTestCase):
    async def login_admin(self) -> None:
        self.assertEqual((await self.login("admin", "Admin@123")).status_code, 200)

    async def test_non_admin_cannot_manage_users(self) -> None:
        await self.login("analyst", "Analyst@123")

        listed = await self.client.get("/api/v1/users")
        created = await self.client.post("/api/v1/users", json={
            "username": "newbie", "password": "Newbie@123", "role": "analyst"})

        for response in (listed, created):
            with self.subTest(path=response.request.url.path):
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_pr_cannot_manage_users(self) -> None:
        """公关明确不可管理账号（PRD 第 2 章）。"""
        await self.login("pr", "Pr@12345")
        response = await self.client.get("/api/v1/users")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_admin_creates_user(self) -> None:
        await self.login_admin()
        response = await self.client.post("/api/v1/users", json={
            "username": "Newbie", "password": "Newbie@123",
            "display_name": "新同事", "role": "pr"})

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["username"], "newbie")     # 统一小写存储
        self.assertEqual(body["role"], "pr")
        self.assertEqual(body["status"], "active")
        self.assertNotIn("password", body)
        self.assertNotIn("password_hash", body)

    async def test_duplicate_username_rejected(self) -> None:
        await self.login_admin()
        response = await self.client.post("/api/v1/users", json={
            "username": "ADMIN", "password": "Newbie@123", "role": "analyst"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "BIZ_202")

    async def test_weak_password_rejected(self) -> None:
        """两层校验：schema 管长度，服务层管「字母 + 数字」这类策略。

        这里用 8 位纯字母的口令——它过得去 schema，必须被服务层拦下，
        且提示是中文策略说明而不是 Pydantic 的英文原文。
        """
        await self.login_admin()
        response = await self.client.post("/api/v1/users", json={
            "username": "weakling", "password": "abcdefgh", "role": "analyst"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")
        self.assertIn("口令强度不足", response.json()["message"])

    async def test_password_shorter_than_schema_minimum(self) -> None:
        await self.login_admin()
        response = await self.client.post("/api/v1/users", json={
            "username": "tiny", "password": "short", "role": "analyst"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")

    async def test_invalid_role_rejected(self) -> None:
        await self.login_admin()
        response = await self.client.post("/api/v1/users", json={
            "username": "roleless", "password": "Newbie@123", "role": "root"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")

    async def test_list_users_paginates_and_filters(self) -> None:
        await self.login_admin()

        all_users = await self.client.get("/api/v1/users", params={"page_size": 2})
        self.assertEqual(all_users.status_code, 200)
        self.assertEqual(all_users.json()["total"], 3)
        self.assertEqual(len(all_users.json()["items"]), 2)

        only_admin = await self.client.get("/api/v1/users", params={"role": "admin"})
        self.assertEqual(only_admin.json()["total"], 1)

        keyword = await self.client.get("/api/v1/users", params={"keyword": "analy"})
        self.assertEqual(keyword.json()["total"], 1)

    async def test_admin_can_disable_other_user(self) -> None:
        await self.login_admin()
        response = await self.client.put(f"/api/v1/users/{self.analyst.user_id}",
                                         json={"status": "disabled"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "disabled")

    async def test_admin_cannot_disable_self(self) -> None:
        """手一滑把自己锁在门外，没人能救——必须在服务层拦住。"""
        await self.login_admin()
        response = await self.client.put(f"/api/v1/users/{self.admin.user_id}",
                                         json={"status": "disabled"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "BIZ_203")

    async def test_admin_cannot_demote_self(self) -> None:
        await self.login_admin()
        response = await self.client.put(f"/api/v1/users/{self.admin.user_id}",
                                         json={"role": "analyst"})
        self.assertEqual(response.status_code, 409)

    async def test_last_admin_cannot_be_disabled(self) -> None:
        """把管理员减到只剩一个之后，最后这个不能再被停用——否则没人能进用户管理。

        用两个管理员走完这条路径：admin2 先停用 admin（合法，还剩 admin2），
        再想停用/降级自己时被拦下。第二个动作既可能撞「不能改自己」，
        也可能撞「必须保留至少一个管理员」，两者都是 BIZ_203。
        """
        second_admin = make_user("admin2", "Admin2@123", role="admin")
        self.repo = InMemoryUserRepository([self.admin, second_admin])
        self.app.dependency_overrides[get_user_repository] = lambda: self.repo

        self.assertEqual((await self.login("admin2", "Admin2@123")).status_code, 200)

        first = await self.client.put(f"/api/v1/users/{self.admin.user_id}",
                                      json={"status": "disabled"})
        self.assertEqual(first.status_code, 200, "还剩两个管理员时应当允许停用其一")
        self.assertEqual(await self.repo.count_active_admins(), 1)

        last = await self.client.put(f"/api/v1/users/{second_admin.user_id}",
                                     json={"status": "disabled"})
        self.assertEqual(last.status_code, 409)
        self.assertEqual(last.json()["code"], "BIZ_203")

        still_there = await self.repo.get_by_user_id(second_admin.user_id)
        self.assertEqual(still_there.status, "active")   # 拦截是真的生效，不是只报了错

    async def test_last_admin_guard_fires_before_self_guard(self) -> None:
        """仅剩一个管理员时，拦下他的理由应当是「必须保留至少一个管理员」。

        两条规则（不能改自己 / 必须保留一个管理员）都会拦这个动作，
        此处断言提示文案，确认先命中的是后者——它更能说明问题出在哪。
        """
        colleague = make_user("admin3", "Admin3@123", role="admin")
        self.repo = InMemoryUserRepository([self.admin, colleague])
        self.app.dependency_overrides[get_user_repository] = lambda: self.repo

        self.assertEqual((await self.login("admin", "Admin@123")).status_code, 200)

        # 先把伙伴降级：合法，此时 admin 成为唯一的管理员
        demote = await self.client.put(f"/api/v1/users/{colleague.user_id}",
                                       json={"role": "analyst"})
        self.assertEqual(demote.status_code, 200)
        self.assertEqual(await self.repo.count_active_admins(), 1)

        blocked = await self.client.put(f"/api/v1/users/{self.admin.user_id}",
                                        json={"role": "analyst"})
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("至少一个启用状态的管理员", blocked.json()["message"])

    async def test_unknown_user_update_returns_404(self) -> None:
        await self.login_admin()
        response = await self.client.put("/api/v1/users/u-not-exist",
                                         json={"display_name": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "BIZ_201")

    async def test_reset_password_kills_existing_session(self) -> None:
        """管理员重置口令后，该用户的既有会话立即失效。"""
        analyst_client = AsyncClient(transport=ASGITransport(app=self.app),
                                     base_url="http://test")
        self.addAsyncCleanup(analyst_client.aclose)

        self.assertEqual((await analyst_client.post(
            LOGIN_PATH, json={"username": "analyst", "password": "Analyst@123"}
        )).status_code, 200)

        await self.login_admin()
        reset = await self.client.post(
            f"/api/v1/users/{self.analyst.user_id}/reset-password",
            json={"new_password": "Fresh@Pass9"})
        self.assertEqual(reset.status_code, 200)

        stale = await analyst_client.get("/api/v1/auth/me")
        self.assertEqual(stale.status_code, 401)

        # 新旧口令的可用性也一并确认：改的是口令，不是把人锁死
        self.assertEqual((await self.login("analyst", "Analyst@123")).status_code, 401)
        self.assertEqual((await self.login("analyst", "Fresh@Pass9")).status_code, 200)


class TraceAndErrorBodyTest(ApiTestCase):
    async def test_unauthorized_body_shape(self) -> None:
        response = await self.client.get("/api/v1/users")
        body = response.json()

        self.assertEqual(set(body.keys()), {"code", "message", "detail", "trace_id"})
        self.assertTrue(body["trace_id"])
        self.assertEqual(response.headers["X-Trace-Id"], body["trace_id"])

    async def test_upstream_trace_id_is_reused(self) -> None:
        response = await self.client.get("/api/v1/users",
                                        headers={"X-Trace-Id": "abc123def456"})
        self.assertEqual(response.headers["X-Trace-Id"], "abc123def456")
        self.assertEqual(response.json()["trace_id"], "abc123def456")

    async def test_expired_cookie_reports_auth_002(self) -> None:
        """过期与非法分开：前端对二者的文案不同。"""
        from app.services.auth.tokens import issue_session
        from engines.contracts.config import get_settings

        token = issue_session(self.analyst.user_id, self.analyst.password_hash,
                              settings=get_settings())
        self.client.cookies.set("sentiment_session", token)

        # 把有效期改成负数来模拟过期（比睡等真实时长快）
        from unittest.mock import patch

        with patch.object(get_settings(), "session_max_age_seconds", -1):
            response = await self.client.get("/api/v1/auth/me")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "AUTH_002")

    async def test_unknown_path_uses_business_code(self) -> None:
        response = await self.client.get("/api/v1/nope")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "BIZ_201")

    async def test_unknown_path_message_is_chinese(self) -> None:
        """内置异常的 detail 是英文，不能直接当 message 发给用户。"""
        response = await self.client.get("/api/v1/nope")
        self.assertEqual(response.json()["message"], "资源不存在")
        self.assertEqual(response.json()["detail"], "Not Found")

    async def test_broken_body_always_reports_biz_400(self) -> None:
        """请求体坏掉的两条路径都必须回 BIZ_400 + 中文文案。

        两条路径的错误类型不同，联调时都踩到过：
          - **非 UTF-8 字节**（真实 curl 发 GBK 中文时命中）：FastAPI 抛
            `HTTPException(400, "There was an error parsing the body")`，
            由于 400 未映射到本项目错误码，实测返回过
            `{"code":"SYS_500","message":"There was an error parsing the body"}`
            ——错误码和文案双错。
          - **合法 UTF-8 但不是 JSON**：走 RequestValidationError（422）。
        """
        not_utf8 = await self.client.post(
            LOGIN_PATH, content=b'{"username":"\xd1\xe9"}',
            headers={"Content-Type": "application/json"})
        self.assertEqual(not_utf8.status_code, 400)
        self.assertEqual(not_utf8.json()["code"], "BIZ_400")
        self.assertEqual(not_utf8.json()["message"], "请求参数不合法")

        not_json = await self.client.post(
            LOGIN_PATH, content=b"{not-json",
            headers={"Content-Type": "application/json"})
        self.assertEqual(not_json.status_code, 422)
        self.assertEqual(not_json.json()["code"], "BIZ_400")
        self.assertEqual(not_json.json()["message"], "请求体不是合法的 JSON")


class AuthCoverageTest(ApiTestCase):
    """接口鉴权覆盖率 100%（设计规格 8.1）：除登录外全部默认拒绝。"""

    async def test_every_protected_path_requires_login(self) -> None:
        paths = self.app.openapi()["paths"]

        api_paths = [p for p in paths if p.startswith("/api/v1")]
        self.assertTrue(api_paths, "OpenAPI 里没有 /api/v1 路径，测试本身失效了")

        for path in api_paths:
            if path in PUBLIC_PATHS:
                continue
            with self.subTest(path=path):
                methods = [m for m in paths[path] if m in ("get", "post", "put", "delete")]
                self.assertTrue(methods, f"{path} 没有任何方法定义")
                for method in methods:
                    # 只发 GET：守卫在依赖解析阶段执行，早于请求体校验，
                    # 因此 GET 足以验证「未登录会被拦」，且不受请求体影响
                    response = await self.client.request(method.upper(), path)
                    self.assertEqual(response.status_code, 401,
                                     f"{method.upper()} {path} 未受登录守卫保护")
                    self.assertEqual(response.json()["code"], "AUTH_001")

    async def test_guard_is_declared_at_router_group_level(self) -> None:
        """守卫挂在路由组上而非逐条路由上——新增路由忘记挂依赖的漏子就堵死了。

        这里断言的是路由组的 `dependencies`（而非遍历 `app.routes`）：
        FastAPI 0.143 起 include_router 会插入惰性的 `_IncludedRouter` 包装对象，
        遍历取 `.path` 会拿到 None（见 README 的环境注意事项）。
        """
        from fastapi.params import Depends as DependsParam

        from app.main import _build_routers
        from app.security import require_authenticated

        _, protected = _build_routers()
        self.assertTrue(protected.routes, "受保护路由组为空")

        declared = {d.dependency for d in protected.dependencies
                    if isinstance(d, DependsParam)}
        self.assertIn(require_authenticated, declared)

    async def test_health_and_login_remain_public(self) -> None:
        health = await self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)

        # 登录不需要会话（凭据校验失败是另一回事，401 的码也应是 AUTH_004）
        login = await self.login("analyst", "WrongPass1")
        self.assertEqual(login.json()["code"], "AUTH_004")


class AuditWiringTest(ApiTestCase):
    """FR-04 的落点：登录与用户管理真的会留痕（T-20 的装饰器已挂上）。"""

    def audit_rows_for(self, action: str) -> list:
        return [row for row in self.audit_rows if row.action == action]

    async def test_successful_login_is_audited(self) -> None:
        await self.login("analyst", "Analyst@123")

        rows = self.audit_rows_for("login")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.user_id, self.analyst.user_id)   # 登录后补登操作人
        self.assertEqual(row.target, "username:analyst")
        self.assertEqual(row.ip, "127.0.0.1")                 # 中间件绑定的来源 IP
        self.assertIn("analyst", row.after_value)

    async def test_failed_login_is_audited_without_actor(self) -> None:
        """失败登录是审计的核心场景：要记下来，但不能记出口令。"""
        await self.login("analyst", "WrongPass1")

        rows = self.audit_rows_for("login")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].user_id)                    # 此时还不知道是谁
        self.assertEqual(rows[0].target, "username:analyst")
        self.assertIn("AUTH_004", rows[0].after_value)
        self.assertNotIn("WrongPass1", rows[0].after_value)

    async def test_unknown_user_login_is_audited(self) -> None:
        await self.login("nobody", "WrongPass1")
        self.assertEqual(self.audit_rows_for("login")[0].target, "username:nobody")

    async def test_user_creation_is_audited_without_credentials(self) -> None:
        self.assertEqual((await self.login("admin", "Admin@123")).status_code, 200)
        await self.client.post("/api/v1/users", json={
            "username": "newbie", "password": "Newbie@123", "role": "pr"})

        row = self.audit_rows_for("user_create")[0]
        # 新增时用用户名做 target（user_id 在函数内部生成，装饰器看不到）
        self.assertEqual(row.target, "user:newbie")
        self.assertEqual(row.user_id, self.admin.user_id)
        self.assertIn("pr", row.after_value)
        self.assertIsNone(row.before_value)                   # 新增没有前值
        self.assertNotIn("Newbie@123", str(row.before_value) + str(row.after_value))
        self.assertNotIn("scrypt$", row.after_value)          # 口令哈希不进审计表

    async def test_role_change_records_before_and_after(self) -> None:
        """FR-04 验收口径：可查到操作人、时间、变更前后值。"""
        self.assertEqual((await self.login("admin", "Admin@123")).status_code, 200)
        await self.client.put(f"/api/v1/users/{self.analyst.user_id}",
                              json={"role": "admin"})

        row = self.audit_rows_for("user_update")[0]
        self.assertEqual(row.target, f"user:{self.analyst.user_id}")
        self.assertEqual(row.user_id, self.admin.user_id)
        self.assertTrue(row.operated_at)
        self.assertEqual(json.loads(row.before_value)["role"], "analyst")
        self.assertEqual(json.loads(row.after_value)["role"], "admin")

    async def test_rejected_operation_is_audited_and_still_fails(self) -> None:
        """被业务规则拒绝的操作也要留痕（停用最后一个管理员）。"""
        self.assertEqual((await self.login("admin", "Admin@123")).status_code, 200)
        response = await self.client.put(f"/api/v1/users/{self.admin.user_id}",
                                         json={"status": "disabled"})

        self.assertEqual(response.status_code, 409)
        row = self.audit_rows_for("user_update")[0]
        self.assertIn("BIZ_203", row.after_value)

    async def test_password_reset_audited_without_hashes(self) -> None:
        self.assertEqual((await self.login("admin", "Admin@123")).status_code, 200)
        await self.client.post(f"/api/v1/users/{self.analyst.user_id}/reset-password",
                               json={"new_password": "Fresh@Pass9"})

        row = self.audit_rows_for("user_reset_password")[0]
        self.assertEqual(row.target, f"user:{self.analyst.user_id}")
        self.assertTrue(json.loads(row.after_value)["password_reset"])
        self.assertNotIn("scrypt$", str(row.before_value) + str(row.after_value))

    async def test_permission_denial_has_no_audit_row(self) -> None:
        """越权在依赖层就被挡下，没有进入任何被审计的服务调用。

        这是**已知边界**：审计（FR-04）覆盖的是「关键业务操作」，
        越权尝试由访问日志承载（401/403 的访问记录 + trace_id）。
        若要单独收敛成安全事件，应在 `require_permission` 里加记录，
        而不是指望业务装饰器——那时业务函数根本没有被调用。
        """
        await self.login("analyst", "Analyst@123")
        await self.client.get("/api/v1/users")

        self.assertEqual(self.audit_rows_for("user_create"), [])
        self.assertEqual(self.audit_rows_for("user_update"), [])
        self.assertEqual(self.audit_rows_for("user_reset_password"), [])


class RepositoryParityTest(unittest.IsolatedAsyncioTestCase):
    """内存替身与内存实现的行为一致性（避免"测试全绿、线上是坏的"）。"""

    async def test_update_and_lookup_roundtrip(self) -> None:
        repo = InMemoryUserRepository([make_user("someone", role="analyst")])
        user = await repo.get_by_username("someone")

        updated = await repo.update(user.user_id, {"role": "admin"})
        self.assertEqual(updated.role, "admin")
        self.assertEqual((await repo.get_by_user_id(user.user_id)).role, "admin")

    async def test_missing_user_returns_none(self) -> None:
        repo = InMemoryUserRepository()
        self.assertIsNone(await repo.get_by_username("nobody"))
        self.assertIsNone(await repo.update("u-x", {"role": "admin"}))

    async def test_count_active_admins(self) -> None:
        repo = InMemoryUserRepository([
            make_user("a1", role="admin"),
            make_user("a2", role="admin", status="disabled"),
            make_user("a3", role="analyst"),
        ])
        self.assertEqual(await repo.count_active_admins(), 1)


if __name__ == "__main__":
    unittest.main()
