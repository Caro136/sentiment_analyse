"""请求与响应模型（Pydantic）。

与 engines/contracts 的区别：
  - 本包定义**对外接口**的契约（HTTP 入参出参）
  - engines/contracts 定义**模块之间**的契约（事件、证据模型、配置）

已有模块：`common`（错误体、分页）、`auth`（认证与用户管理，T-19）。
"""
