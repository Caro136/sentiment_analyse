"""数据库访问层：ORM 模型、会话管理、连接池。

分层约定：
  - 模型（models/）只描述表结构，不含查询逻辑
  - 会话（session.py）提供连接与事务边界
  - 查询逻辑放在各领域模块（如 analytics/repository.py），不写进模型

⚠️ 原始区（weibo_note / douyin_aweme / news_article / forum_thread 等 9 张表）
   在本项目内**只读**（设计规格 ADR-03）。ORM 中不为其定义写操作，
   任何模块都不得对其执行 UPDATE / DELETE。
"""
