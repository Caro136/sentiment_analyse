"""邮件通知：`smtplib` 直连（设计规格 附录 C.3 / D-03「邮件优先」）。

三个必须守住的点：

1. **失败要重试，重试要退避**。SMTP 抖动、限流都常见，按 5s / 15s / 45s 退避
   重试 3 次；仍失败则**降级为纯站内消息**（告警记录本身就在库里，站内始终可用），
   并把 `notify_status` 标成 `degraded`、失败原因写进 `notify_error`——
   降级必须可见，否则「告警发了但没人收到」这件事会被静默吞掉。
2. **同步阻塞的 smtplib 要扔进线程池**。它没有异步实现，直接在事件循环里调用
   会把整个服务的请求处理卡住（最长 10 秒超时 × 4 次尝试）。
3. **密钥不进日志**。异常文本先过 `mask_secret` 再记录。

未配置 SMTP 时**不视为失败**：返回 `skipped`，由调用方把站内消息标为可用。
开发环境不该因为没配邮箱就一路报错。
"""

import asyncio
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText

from loguru import logger

from engines.common.logging import mask_secret
from engines.contracts.config import Settings, get_settings

__all__ = ["SEND_STATUS_SENT", "SEND_STATUS_DEGRADED", "SEND_STATUS_SKIPPED", "SendResult",
           "send_alert_mail", "notify"]

SEND_STATUS_SENT = "sent"
SEND_STATUS_DEGRADED = "degraded"
SEND_STATUS_SKIPPED = "skipped"

# 退避间隔（秒）。三次重试的总等待约 65 秒，仍在「3 分钟内告警」的指标内
RETRY_DELAYS = (5, 15, 45)
SMTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class SendResult:
    status: str
    attempts: int = 0
    error: str | None = None

    @property
    def delivered(self) -> bool:
        return self.status == SEND_STATUS_SENT


def _smtp_configured(settings: Settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password
                and settings.alert_mail_to)


def _send_once(settings: Settings, subject: str, body: str) -> None:
    """一次同步发送。异常原样抛出，由调用方决定是否重试。"""
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = settings.smtp_user
    message["To"] = settings.alert_mail_to

    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                          timeout=SMTP_TIMEOUT_SECONDS) as server:
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_user, [settings.alert_mail_to], message.as_string())


async def send_alert_mail(subject: str, body: str, *,
                          settings: Settings | None = None) -> SendResult:
    """发送告警邮件，失败按退避重试；返回结果而不抛异常。"""
    cfg = settings or get_settings()
    if not _smtp_configured(cfg):
        logger.warning("邮件通知未配置（SMTP_USER / SMTP_PASSWORD / ALERT_MAIL_TO），"
                       "本次告警仅站内可见")
        return SendResult(status=SEND_STATUS_SKIPPED, error="SMTP 未配置")

    last_error = ""
    for attempt, delay in enumerate((0, *RETRY_DELAYS), start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            # smtplib 是同步阻塞的：扔进线程池，别卡住事件循环
            await asyncio.to_thread(_send_once, cfg, subject, body)
        except Exception as exc:  # noqa: BLE001 —— 任何异常都按「投递失败」处理
            last_error = mask_secret(f"{type(exc).__name__}: {exc}")[:300]
            logger.warning("告警邮件投递失败（第 {} 次）：{}", attempt, last_error)
            continue

        logger.info("告警邮件已投递（第 {} 次尝试）：{}", attempt, cfg.alert_mail_to)
        return SendResult(status=SEND_STATUS_SENT, attempts=attempt)

    logger.error("告警邮件重试 {} 次后仍失败，降级为站内消息：{}",
                 len(RETRY_DELAYS), last_error)
    return SendResult(status=SEND_STATUS_DEGRADED,
                      attempts=len(RETRY_DELAYS) + 1, error=last_error)


async def notify(subject: str, body: str, *,
                 settings: Settings | None = None) -> SendResult:
    """通知下发的统一入口（当前只有邮件渠道，站内即告警记录本身）。"""
    return await send_alert_mail(subject, body, settings=settings)
