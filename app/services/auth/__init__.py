"""认证与权限服务（T-19，对应 PRD FR-01~FR-03）。

模块划分：

| 模块 | 职责 | 是否碰数据库 |
|------|------|-------------|
| `passwords` | 口令哈希与强度检查（scrypt） | 否 |
| `tokens`    | 会话票据签发/校验、Cookie 读写 | 否 |
| `repository`| `sys_user` 读写（SQL 只在这里出现） | 是 |
| `service`   | 登录、会话解析、用户管理用例 | 经 repository |
| `seed`      | 演示账号初始化 CLI | 是 |

鉴权的接入层入口（`get_current_user` / `require_permission`）在 `app/security.py`，
权限矩阵在 `engines/contracts/permissions.py`——三者刻意不在同一层：
矩阵要被引擎层引用，依赖注入属于接入层。

未纳入本任务：FR-04 操作审计日志（T-20 的 `@audit` 装饰器）、
FR-05 用户自助改口令（P2，当前由管理员重置口令覆盖）。
"""
