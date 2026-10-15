"""SQLAlchemy 声明式基类。

所有 ORM 模型继承 `Base`。模型定义必须与 `sql/analysis/` 与 `sql/business/` 下的
DDL 保持一致——**DDL 是权威定义**，ORM 是它的代码映射。

改表结构时的顺序：先改 DDL → 再改 ORM → 跑 `python -m engines.common.db.check` 校验一致。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """ORM 基类。"""

    def to_dict(self) -> dict:
        """转为字典（仅列字段），便于日志与调试输出。"""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}
