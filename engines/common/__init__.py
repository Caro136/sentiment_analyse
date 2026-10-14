"""公共能力：日志、事件总线、LLM 客户端、重试器、报告 IO、通用节点。

不含业务语义——放这里的代码应当对任何业务场景都成立。

已实现：logging（日志初始化与密钥脱敏）
待实现：eventing（事件总线，T-03）、llm_client（模型客户端，T-04）、
runtime/call_retry（重试，T-04）、io/report_io（报告读写，T-17）
"""
