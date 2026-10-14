"""输出层：把生成的记录写成可直接导入 MySQL 的 SQL 文件，以及 JSONL 样本。

SQL 输出按信源拆分成多个文件，便于按需导入与人工核对；
90_import_all.sql 用 source 语句把它们串起来，一条命令完成全量导入。
"""

import json
import os

import schema

# 每条 INSERT 语句包含的行数，兼顾文件可读性与导入效率
BATCH_SIZE = 200

# 信源 -> 输出文件名
SOURCE_FILES = {
    schema.SOURCE_SOCIAL: "10_social_media.sql",
    schema.SOURCE_VIDEO: "20_short_video.sql",
    schema.SOURCE_NEWS: "30_news_portal.sql",
    schema.SOURCE_FORUM: "40_industry_forum.sql",
}


def sql_literal(value):
    """把 Python 值转成 MySQL 字面量。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # MySQL 默认把反斜杠视为转义符，两者都要转义
    text = text.replace("\\", "\\\\").replace("'", "\\'")
    text = text.replace("\n", "\\n").replace("\r", "\\r")
    return "'" + text + "'"


def _insert_statements(table_name, rows):
    """生成批量 INSERT 语句。"""
    spec = schema.TABLES[table_name]
    cols = ", ".join(f"`{c}`" for c in spec.columns)
    statements = []
    for start in range(0, len(rows), BATCH_SIZE):
        chunk = rows[start:start + BATCH_SIZE]
        values = []
        for row in chunk:
            values.append("(" + ", ".join(
                sql_literal(row.get(c)) for c in spec.columns) + ")")
        statements.append(
            f"INSERT INTO `{table_name}` ({cols}) VALUES\n"
            + ",\n".join(values) + ";")
    return statements


def _file_header(title, note=""):
    lines = [
        f"-- {title}",
        "-- 由 data/generator 生成，请勿手工编辑",
        f"-- 数据库：{schema.DATABASE_NAME}",
    ]
    if note:
        lines.append(f"-- {note}")
    lines += ["", "SET NAMES utf8mb4;", f"USE `{schema.DATABASE_NAME}`;", ""]
    return "\n".join(lines)


def write_schema_file(out_dir):
    path = os.path.join(out_dir, "00_schema.sql")
    with open(path, "w", encoding="utf-8") as f:
        f.write(schema.build_schema_sql())
    return path


def write_source_files(data, out_dir):
    """按信源输出数据文件，返回生成的文件名列表。"""
    written = []
    for source, filename in SOURCE_FILES.items():
        tables = schema.SOURCE_TABLES[source]
        parts = [_file_header(
            f"{schema.SOURCE_LABELS[source]}（{tables['content']} / {tables['comment']}）",
            "课程已有的表结构原样保留，两个平台字段命名差异为刻意保留，供接入层映射使用"
            if source in (schema.SOURCE_SOCIAL, schema.SOURCE_VIDEO) else
            "新增信源表，命名与类型习惯对齐课程表结构")]
        for table_name in (tables["content"], tables["comment"]):
            rows = data.rows[table_name]
            if not rows:
                continue
            parts.append(f"-- 共 {len(rows)} 行")
            parts.extend(_insert_statements(table_name, rows))
            parts.append("")
        with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as f:
            f.write("\n".join(parts))
        written.append(filename)
    return written


def write_account_file(data, out_dir):
    filename = "50_platform_account.sql"
    parts = [_file_header(
        "平台账号主数据（platform_account）",
        "补齐课程表缺失的粉丝量与认证信息，供账号权重评估使用")]
    parts.append(f"-- 共 {len(data.accounts)} 行")
    parts.extend(_insert_statements("platform_account", data.accounts))
    parts.append("")
    with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return filename


def write_import_all(out_dir, data_files):
    """生成一条命令导入全部分片的总入口。"""
    ordered = ["00_schema.sql", "50_platform_account.sql"] + sorted(data_files)
    lines = [
        "-- 全量导入入口：mysql -u <user> -p < data/sql/90_import_all.sql",
        "-- 请将本文件与其余分片放在同一目录下执行",
        "",
    ]
    for name in ordered:
        lines.append(f"source {name};")
    lines.append("")
    path = os.path.join(out_dir, "90_import_all.sql")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return "90_import_all.sql"


def write_samples_jsonl(samples, path):
    """输出带隐藏标签的样本，供评测集构造与人工抽查使用。"""
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    return path
