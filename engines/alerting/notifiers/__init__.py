"""通知渠道。

当前实现：**邮件**（D-03「邮件优先」，smtplib 直连）。
站内消息不需要渠道实现——告警记录本身就在 `alert_record` 里，前端拉列表即可。

新增渠道（企业微信 / 飞书 / SMS）时的约定：实现一个 `async def notify(subject, body)
-> SendResult` 的函数，在 `service._persist` 的通知环节按配置逐个调用，
并把每个渠道的结果合并进 `notify_status`（任一成功即 `sent`，全失败才是 `degraded`）。
"""

from engines.alerting.notifiers import email_notifier

__all__ = ["email_notifier"]
