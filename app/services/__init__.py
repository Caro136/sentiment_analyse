"""服务层：业务用例编排。

把接口请求转换成具体业务动作，组织一个或多个引擎能力完成用例。
不实现算法，不直接写 SQL。

已实现：`auth`（认证、会话、用户管理，T-19）、`realtime`（SSE 扇出，T-18）。
规划中：topic、content、event、ticket、alert、report、dashboard、config。
"""
