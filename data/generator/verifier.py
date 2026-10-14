"""生成结果自检。

不需要 MySQL 即可运行：全部检查都基于内存中的生成结果与磁盘上的输出文件。
目的是在数据交付前把「评论指向不存在的内容」「时间越界」
「情感分布偏离剧本」「信源缺一类」这类问题挡在导入之前。
"""

import json
import os
from datetime import datetime, timedelta, timezone

import schema
import scenarios
from scenarios import SENTIMENT_NEGATIVE, SENTIMENT_NEUTRAL, SENTIMENT_POSITIVE

CST = timezone(timedelta(hours=8))
SENTIMENT_TOLERANCE = 0.12      # 剧本期望分布与实际分布的允许偏差


class CheckResult:
    def __init__(self):
        self.items = []

    def add(self, name, passed, detail=""):
        self.items.append((name, bool(passed), detail))

    @property
    def passed(self):
        return all(ok for _, ok, _ in self.items)

    def render(self):
        lines = ["", "=" * 68, "生成结果自检", "=" * 68]
        for name, ok, detail in self.items:
            mark = "PASS" if ok else "FAIL"
            lines.append(f"[{mark}] {name}")
            if detail:
                for line in str(detail).splitlines():
                    lines.append(f"       {line}")
        lines.append("-" * 68)
        lines.append("自检结论：" + ("全部通过" if self.passed else "存在未通过项，请检查上方 FAIL 条目"))
        lines.append("=" * 68)
        return "\n".join(lines)


def _biz_ids(rows, key):
    return {row[key] for row in rows}


def check_sources(data, result):
    """4 类信源都必须有内容与评论数据。"""
    detail = []
    ok = True
    for source, tables in schema.SOURCE_TABLES.items():
        c = len(data.rows[tables["content"]])
        m = len(data.rows[tables["comment"]])
        label = schema.SOURCE_LABELS[source]
        detail.append(f"{label}：内容 {c} 条 / 评论 {m} 条")
        if c == 0 or m == 0:
            ok = False
    result.add("信源覆盖完整（4 类信源均有内容与评论）", ok, "\n".join(detail))


def check_foreign_keys(data, result):
    """每条评论都必须能追溯到存在的内容宿主。"""
    problems = []

    host_note = _biz_ids(data.rows["weibo_note"], "note_id")
    orphans = [r for r in data.rows["weibo_note_comment"] if r["note_id"] not in host_note]
    if orphans:
        problems.append(f"社交媒体评论存在孤儿记录 {len(orphans)} 条")

    host_aweme = _biz_ids(data.rows["douyin_aweme"], "aweme_id")
    orphans = [r for r in data.rows["douyin_aweme_comment"] if r["aweme_id"] not in host_aweme]
    if orphans:
        problems.append(f"短视频评论存在孤儿记录 {len(orphans)} 条")

    host_article = _biz_ids(data.rows["news_article"], "id")
    orphans = [r for r in data.rows["news_article_comment"]
               if r["article_id"] not in host_article]
    if orphans:
        problems.append(f"新闻门户评论存在孤儿记录 {len(orphans)} 条")

    host_thread = _biz_ids(data.rows["forum_thread"], "thread_id")
    orphans = [r for r in data.rows["forum_post"] if r["thread_id"] not in host_thread]
    if orphans:
        problems.append(f"论坛回帖存在孤儿记录 {len(orphans)} 条")

    result.add("外键完整性（评论均能追溯到内容宿主）", not problems,
               "\n".join(problems) if problems else "全部评论均可追溯到宿主内容")


def check_time_range(data, result):
    """全部时间落在 [结束时间 - 72h, 结束时间] 内。"""
    end_ts = int(data.stats_end_time.timestamp())
    start_ts = end_ts - 72 * 3600
    out_of_range = []

    time_fields = {
        "weibo_note": "create_time",
        "weibo_note_comment": "create_time",
        "douyin_aweme": "create_time",
        "douyin_aweme_comment": "create_time",
        "news_article": "publish_time",
        "news_article_comment": "create_time",
        "forum_thread": "publish_time",
        "forum_post": "create_time",
    }
    for table, field in time_fields.items():
        bad = [r for r in data.rows[table] if not (start_ts <= r[field] <= end_ts)]
        if bad:
            out_of_range.append(f"{table}：{len(bad)} 条越界")

    result.add("时间分布（均落在最近 72 小时内）", not out_of_range,
               "\n".join(out_of_range) if out_of_range else "全部记录时间合法")


def check_sentiment(data, result):
    """各场景的评论情感分布应贴近剧本预期。"""
    stats = {}
    for s in data.samples:
        if s["role"] != "comment":
            continue
        stats.setdefault(s["scenario"], {})
        stats[s["scenario"]][s["sentiment"]] = \
            stats[s["scenario"]].get(s["sentiment"], 0) + 1

    detail = []
    ok = True
    for scenario in scenarios.SCENARIOS:
        counts = stats.get(scenario.key, {})
        total = sum(counts.values())
        if total == 0:
            ok = False
            detail.append(f"{scenario.title}：无评论样本")
            continue
        parts = []
        for cls in (SENTIMENT_POSITIVE, SENTIMENT_NEUTRAL, SENTIMENT_NEGATIVE):
            actual = counts.get(cls, 0) / total
            expect = scenario.sentiment_expect.get(cls, 0.0)
            flag = "" if abs(actual - expect) <= SENTIMENT_TOLERANCE else " ←偏离"
            if flag:
                ok = False
            parts.append(f"{cls} {actual:.1%}(期望 {expect:.0%}){flag}")
        detail.append(f"{scenario.title}：共 {total} 条 | " + "，".join(parts))
    result.add("情感分布符合剧本预期", ok, "\n".join(detail))


def check_alert_trigger(data, result):
    """验证预警规则可被真实数据触发：存在某小时内负向声量环比增幅 > 50%。"""
    from collections import defaultdict

    buckets = defaultdict(lambda: defaultdict(int))

    # 用内容发布时间统计，更贴近预警引擎的实际输入
    for source, tables in schema.SOURCE_TABLES.items():
        keyword_by_scenario = {sc.keyword: sc.key for sc in scenarios.SCENARIOS}
        for row in data.rows[tables["content"]]:
            key = keyword_by_scenario.get(row.get("source_keyword"))
            if not key:
                continue
            ts = row.get("create_time") or row.get("publish_time")
            biz = row.get("note_id") or row.get("aweme_id") or row.get("id") or row.get("thread_id")
            meta = data.content_index.get(biz, {})
            if meta.get("sentiment") != SENTIMENT_NEGATIVE:
                continue
            buckets[key][ts // 3600] += 1

    # 最小基数门槛：真实的预警引擎不能让 "1 条涨到 2 条" 也触发告警，
    # 否则对照组（政策利好场景）的零星质疑会造成大量假阳性
    MIN_PREV_VOLUME = 3

    triggered = []
    for key, hours in buckets.items():
        ordered = sorted(hours.items())
        for i in range(1, len(ordered)):
            prev, cur = ordered[i - 1][1], ordered[i][1]
            if prev >= MIN_PREV_VOLUME and (cur - prev) / prev > 0.5:
                scenario = next(s for s in scenarios.SCENARIOS if s.key == key)
                hour_label = datetime.fromtimestamp(
                    ordered[i][0] * 3600, CST).strftime("%m-%d %H:%M")
                triggered.append(
                    f"{scenario.title}：{hour_label} 起一小时内新增负向 {cur} 条，"
                    f"前一小时 {prev} 条，环比 +{(cur - prev) / prev:.0%}")
                break

    expect_trigger = any(s.key in ("catering_food_safety", "consumer_electronics")
                         for s in scenarios.SCENARIOS)
    ok = bool(triggered) if expect_trigger else True
    result.add("预警可触发（存在单小时负向声量环比增幅 > 50%）", ok,
               "\n".join(triggered[:3]) if triggered else "未发现满足条件的时段")


def _split_values_row(text):
    """把 "(v1, v2, ...)" 拆成值列表，正确处理引号内的逗号与反斜杠转义。"""
    text = text.strip()
    if not (text.startswith("(") and text.endswith(")")):
        raise ValueError(f"值行格式异常：{text[:40]}")
    body = text[1:-1]
    values = []
    buf = []
    in_str = False
    i = 0
    while i < len(body):
        ch = body[i]
        if in_str:
            if ch == "\\" and i + 1 < len(body):
                buf.append(body[i:i + 2])
                i += 2
                continue
            if ch == "'":
                in_str = False
            buf.append(ch)
        else:
            if ch == "'":
                in_str = True
                buf.append(ch)
            elif ch == ",":
                values.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        i += 1
    if in_str:
        raise ValueError("字符串引号未闭合")
    values.append("".join(buf).strip())
    return values


def _parse_columns(line):
    start = line.index("(")
    end = line.rindex(")")
    return [c.strip().strip("`") for c in line[start + 1:end].split(",")]


def check_sql_structure(sql_dir, result):
    """逐行校验生成的 INSERT：列名与 schema 一致、值个数匹配、引号闭合。

    这是在没有数据库凭据时能做的最严格检查——转义错误、列错位、
    字符串未闭合都会在这里暴露，而它们恰恰是导入失败最常见的原因。
    """
    problems = []
    table_rows = {}

    for fname in sorted(os.listdir(sql_dir)):
        if not fname.endswith(".sql") or fname in ("00_schema.sql", "90_import_all.sql"):
            continue
        path = os.path.join(sql_dir, fname)
        cur_cols = None
        cur_table = None
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                stripped = line.strip()
                if stripped.startswith("INSERT INTO"):
                    cur_table = stripped.split("`")[1]
                    cur_cols = _parse_columns(stripped)
                    expected = schema.TABLES[cur_table].columns
                    if cur_cols != expected:
                        problems.append(
                            f"{fname}:{lineno} 表 {cur_table} 列定义与 schema 不一致")
                    table_rows[cur_table] = table_rows.get(cur_table, 0)
                elif (cur_cols and stripped and stripped not in (";", ")")
                      and not stripped.startswith("--")):
                    row_text = stripped.rstrip(",").rstrip(";")
                    try:
                        values = _split_values_row(row_text)
                    except ValueError as exc:
                        problems.append(f"{fname}:{lineno} {exc}")
                        continue
                    if len(values) != len(cur_cols):
                        problems.append(
                            f"{fname}:{lineno} 表 {cur_table} 值个数 {len(values)} "
                            f"≠ 列数 {len(cur_cols)}")
                    table_rows[cur_table] += 1

    detail = ["各表 INSERT 行数：" + ", ".join(
        f"{k} {v}" for k, v in sorted(table_rows.items()))]
    if problems:
        detail.append(f"发现 {len(problems)} 处问题，前 10 条：")
        detail.extend(problems[:10])
    result.add("SQL 结构完整性（列匹配 / 引号闭合 / 值个数）", not problems,
               "\n".join(detail))
    return table_rows


def check_output_files(data, sql_dir, eval_dir, result):
    """输出文件存在性、SQL 行数、评测集条数与均衡性。"""
    detail = []
    ok = True

    expected_sql = ["00_schema.sql", "10_social_media.sql", "20_short_video.sql",
                    "30_news_portal.sql", "40_industry_forum.sql",
                    "50_platform_account.sql", "90_import_all.sql"]
    missing = [f for f in expected_sql
               if not os.path.exists(os.path.join(sql_dir, f))]
    if missing:
        ok = False
        detail.append("缺少 SQL 文件：" + ", ".join(missing))
    else:
        detail.append(f"SQL 文件齐全（{len(expected_sql)} 个）")

    for f in expected_sql:
        path = os.path.join(sql_dir, f)
        if os.path.exists(path) and os.path.getsize(path) == 0:
            ok = False
            detail.append(f"{f} 为空文件")

    # 评测集
    sent_path = os.path.join(eval_dir, "sentiment_eval.jsonl")
    risk_path = os.path.join(eval_dir, "risk_eval.jsonl")
    if not os.path.exists(sent_path) or not os.path.exists(risk_path):
        ok = False
        detail.append("缺少评测集文件")
    else:
        with open(sent_path, encoding="utf-8") as f:
            sent = [json.loads(line) for line in f]
        with open(risk_path, encoding="utf-8") as f:
            risk = [json.loads(line) for line in f]
        cls_counts = {}
        hard = 0
        for r in sent:
            cls_counts[r["gold_sentiment"]] = cls_counts.get(r["gold_sentiment"], 0) + 1
            if r["difficulty"] == "hard":
                hard += 1
        balanced = all(v >= 80 for v in cls_counts.values())
        detail.append(f"情感评测集 {len(sent)} 条，分布 {cls_counts}，"
                      f"困难样本 {hard} 条")

        level_dist = {lv: sum(1 for r in risk if r["gold_risk_level"] == lv)
                      for lv in ("S", "A", "B", "C")}
        scenario_dist = {}
        for r in risk:
            scenario_dist[r["scenario"]] = scenario_dist.get(r["scenario"], 0) + 1
        idx_values = [r["gold_intensity_factors"]["intensity_index"] for r in risk]
        detail.append(f"风险评测集 {len(risk)} 条，等级分布 {level_dist}")
        detail.append(f"风险评测集场景分布 {scenario_dist}，"
                      f"烈度指数 {min(idx_values):.2f}~{max(idx_values):.2f}，"
                      f"含高权重账号 {sum(1 for r in risk if r['kol_involved'])} 条")
        # 场景覆盖：3 个场景都必须出现在风险评测集中
        if len(scenario_dist) < 3:
            ok = False
            detail.append("风险评测集场景覆盖不足（未覆盖全部 3 个场景）")
        if len(sent) < 300:
            ok = False
            detail.append(f"情感评测集不足 300 条（当前 {len(sent)}）")
        if not balanced:
            ok = False
            detail.append("情感评测集三类分布不均衡（存在低于 80 条的类别）")
        if hard < 60:
            ok = False
            detail.append(f"困难样本不足 60 条（当前 {hard}）")
        if len(risk) < 100:
            ok = False
            detail.append(f"风险评测集不足 100 条（当前 {len(risk)}）")

    result.add("输出文件与评测集达标", ok, "\n".join(detail))


def check_row_counts(data, table_rows, result):
    """交叉核对：SQL 文件解析出的行数必须与内存中的生成结果一致。"""
    mismatches = []
    for table in schema.TABLE_ORDER:
        expected = len(data.accounts) if table == "platform_account" \
            else len(data.rows[table])
        actual = table_rows.get(table, 0)
        if expected != actual:
            mismatches.append(f"{table}：内存 {expected} 行 vs SQL 文件 {actual} 行")
    result.add("SQL 文件行数与生成结果一致", not mismatches,
               "\n".join(mismatches) if mismatches else "全部 9 张表行数一致")


def verify(data, sql_dir, eval_dir):
    """执行全部检查，返回 CheckResult。"""
    if not hasattr(data, "stats_end_time") or data.stats_end_time is None:
        raise RuntimeError("生成结果缺少 stats_end_time，无法校验时间范围")
    result = CheckResult()
    check_sources(data, result)
    check_foreign_keys(data, result)
    check_time_range(data, result)
    check_sentiment(data, result)
    check_alert_trigger(data, result)
    table_rows = check_sql_structure(sql_dir, result)
    check_row_counts(data, table_rows, result)
    check_output_files(data, sql_dir, eval_dir, result)
    return result
