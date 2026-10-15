"""运行配置路由（API-29，FR-53）。

能读能写的都是**白名单内的非敏感参数**（烈度权重、向量开关、搜索供应商、
OCR 超时、日志级别）；密钥类配置不在白名单，也不该在这里被修改——见
`app/services/monitoring/runtime_config.py` 的说明。

权限：`config:read` / `config:write` 都只给管理员（运行配置属于运维面）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.schemas.monitoring import ConfigItem, ConfigUpdateRequest, ConfigView
from app.security import require_permission
from app.services.auth.repository import UserRecord
from app.services.monitoring import runtime_config as service
from engines.contracts.permissions import Permission

router = APIRouter(prefix="/api/v1/config", tags=["config"])

_ReadConfig = Annotated[UserRecord, Depends(require_permission(Permission.CONFIG_READ))]
_WriteConfig = Annotated[UserRecord, Depends(require_permission(Permission.CONFIG_WRITE))]


def _to_view(entries) -> ConfigView:
    overridden = [e.key for e in entries if e.overridden]
    return ConfigView(
        items=[ConfigItem(key=e.key, value=e.value, value_type=e.value_type,
                          label=e.label, description=e.description,
                          overridden=e.overridden)
               for e in entries],
        overridden_keys=overridden,
    )


@router.get("", response_model=ConfigView, summary="读取可热更新的运行配置")
async def read_config(_actor: _ReadConfig) -> ConfigView:
    """白名单内配置的当前取值。`overridden=true` 表示该键被运行时热更新覆盖过。"""
    return _to_view(service.read_config())


@router.post("", response_model=ConfigView, summary="热更新运行配置")
async def update_config(payload: ConfigUpdateRequest, _actor: _WriteConfig) -> ConfigView:
    """按白名单热更新运行配置，**立即生效**。

    键不在白名单或取值非法时整批拒绝（错误里会列出可用的键）；改动**不写入 `.env`**，
    重启后回落到环境配置——需要持久化的业务阈值应落在数据库（`alert_rule`，T-24）。
    """
    return _to_view(await service.apply_updates(payload.updates))
