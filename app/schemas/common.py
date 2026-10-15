"""接口通用模型：统一错误体、分页结果、分页参数。

错误体的字段与前端 `front/src/api/client.ts` 的 `ApiErrorBody` 一一对应，
改字段名等于改前端契约。
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

__all__ = ["ErrorBody", "PageResult", "PageQuery", "paginate"]

T = TypeVar("T")

# 与设计规格 8.2 一致：列表接口 page_size 上限 100
MAX_PAGE_SIZE = 100


class ErrorBody(BaseModel):
    """统一错误响应体（设计规格 6.3）。"""

    code: str = Field(description="错误码，如 AUTH_001")
    message: str = Field(description="面向用户的说明")
    detail: str | None = Field(default=None, description="排障用的补充信息（不含敏感数据）")
    trace_id: str = Field(default="", description="请求链路标识，可与后端日志对照")


class PageQuery(BaseModel):
    """分页基参。各业务查询模型继承它，保持分页语义一致。"""

    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE, description="每页条数")


class PageResult(BaseModel, Generic[T]):
    """分页响应。字段名与前端 `PageResult<T>` 一致。"""

    total: int
    items: list[T]
    page: int
    page_size: int


def paginate(total: int, items: list[T], page: int, page_size: int) -> PageResult[T]:
    return PageResult[T](total=total, items=items, page=page, page_size=page_size)
