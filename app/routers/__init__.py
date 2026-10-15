"""API 路由。

路由分两组装配（见 `app/main.py::_build_routers`）：

- **公开组**：`auth.public_router` 的登录接口。全站唯一不需要会话的接口。
- **受保护组**：挂 `require_authenticated` 依赖，加进去即默认要求登录。

已实现：health（存活与就绪探测，注册在根路径）、auth/users（T-19）、
sse（T-18 事件流）。
待实现：topics/channels(T-21)、contents(T-22)、events(T-23)、
alerts(T-24)、tickets(T-25)、dashboard(T-26)、reports(T-27)、research(T-16)。

**新增路由一律加进受保护组**——默认拒绝，忘记加的后果是接口不可达，不是可访问。
"""
