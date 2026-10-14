"""评测集构造（对应 PRD 决策 D-02）。

两类评测集：
  sentiment_eval.jsonl  情感三分类 + 细粒度情绪，正 / 中 / 负均衡
  risk_eval.jsonl       事件风险等级 S/A/B/C 判定

关键设计：**困难样本必须占相当比例**。若评测集只由「愤怒就是负向」这类简单样本构成，
85% 的准确率门槛没有任何区分度。因此反讽、混合情感、纯事实陈述、表情主导、
超短文本、引用转述等边界样本被专门构造并强制纳入。

标注方式：简单样本gold label 来自生成时的隐藏标签（生成器明确知道自己写的是什么）；
困难样本的标签在 scenarios.HARD_SAMPLES 中逐条人工给定，并附判定说明。
风险等级标签由明确的规则推导（见 _judge_risk），依据可复核。
"""

import json
import os
import random
from datetime import datetime, timedelta, timezone

import schema
import scenarios
from scenarios import SENTIMENT_NEGATIVE, SENTIMENT_NEUTRAL, SENTIMENT_POSITIVE

CST = timezone(timedelta(hours=8))

SENTIMENT_TARGET_TOTAL = 330        # 总条数（≥ PRD 要求的 300）
HARD_RATIO = 0.25                   # 困难样本占比，保证门槛有效性
RISK_TARGET_TOTAL = 100

CLS_LABELS = {
    SENTIMENT_POSITIVE: "正向",
    SENTIMENT_NEUTRAL: "中性",
    SENTIMENT_NEGATIVE: "负向",
}


# ---------------------------------------------------------------------------
# 情感评测集
# ---------------------------------------------------------------------------

def _sample_pool(samples):
    """按情感分组，并过滤掉空文本。"""
    pool = {SENTIMENT_POSITIVE: [], SENTIMENT_NEUTRAL: [], SENTIMENT_NEGATIVE: []}
    for s in samples:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        pool.setdefault(s["sentiment"], []).append(s)
    return pool


def build_sentiment_eval(samples, rng, target_total=SENTIMENT_TARGET_TOTAL):
    """分层抽样 + 强制纳入困难样本，输出均衡的情感评测集。"""
    per_class = target_total // 3
    hard_samples = scenarios.get_hard_samples()
    pool = _sample_pool(samples)

    # 困难样本按情感归类
    hard_by_cls = {k: [] for k in CLS_LABELS}
    for h in hard_samples:
        hard_by_cls.setdefault(h["gold_sentiment"], []).append(h)

    # 每类困难样本的目标数量：不超过该类可用量，且不超过总量的 HARD_RATIO
    hard_quota = max(1, int(per_class * HARD_RATIO))
    records = []
    idx = 0

    for cls in (SENTIMENT_POSITIVE, SENTIMENT_NEUTRAL, SENTIMENT_NEGATIVE):
        chosen = []
        available_hard = hard_by_cls.get(cls, [])
        rng.shuffle(available_hard)
        # 只取本类别的困难样本，不跨类补足：跨类补会让同一条文本在评测集里出现两次，
        # 反而稀释有效样本量。困难样本天然分布不均，无需强行按类配平。
        hard_picked = available_hard[:hard_quota]

        for h in hard_picked:
            idx += 1
            chosen.append({
                "id": f"se_{idx:04d}",
                "text": h["text"],
                "source_type": schema.SOURCE_LABELS.get(h["source_type"], h["source_type"]),
                "scenario": h["scenario"],
                "gold_sentiment": h["gold_sentiment"],
                "gold_sentiment_label": CLS_LABELS.get(h["gold_sentiment"], ""),
                "gold_emotion": h["gold_emotion"],
                "difficulty": "hard",
                "note": h["note"],
            })

        # 剩余名额从简单样本池补齐
        remain = per_class - len(chosen)
        candidates = pool.get(cls, [])
        rng.shuffle(candidates)
        picked = candidates[:remain]
        if len(picked) < remain:
            # 该类别样本不足（理论上不会发生），放宽到任意类别补齐总数
            extra = [s for k, v in pool.items() if k != cls for s in v]
            rng.shuffle(extra)
            picked += extra[:remain - len(picked)]

        for s in picked:
            idx += 1
            chosen.append({
                "id": f"se_{idx:04d}",
                "text": s["text"],
                "source_type": schema.SOURCE_LABELS.get(s["source_type"], s["source_type"]),
                "scenario": s["scenario"],
                "gold_sentiment": s["sentiment"],
                "gold_sentiment_label": CLS_LABELS.get(s["sentiment"], ""),
                "gold_emotion": s.get("emotion", ""),
                "difficulty": "normal",
                "note": "",
            })

        records.extend(chosen)

    rng.shuffle(records)
    return records


# ---------------------------------------------------------------------------
# 风险评测集
# ---------------------------------------------------------------------------

def _content_time(source, row):
    if source == schema.SOURCE_NEWS:
        return row["publish_time"]
    if source == schema.SOURCE_FORUM:
        return row["publish_time"]
    return row["create_time"]


def _content_biz_id(source, row):
    if source == schema.SOURCE_SOCIAL:
        return row["note_id"]
    if source == schema.SOURCE_VIDEO:
        return row["aweme_id"]
    if source == schema.SOURCE_NEWS:
        return row["id"]
    return row["thread_id"]


def _collect_scenario_timeline(data):
    """把全部内容按场景收集为 (时间戳, 情感, 账号影响力) 的列表。"""
    timelines = {s.key: [] for s in scenarios.SCENARIOS}
    keyword_to_scenario = {s.keyword: s.key for s in scenarios.SCENARIOS}
    influence_by_account = {
        a["account_id"]: float(a.get("influence_score") or 0) for a in data.accounts
    }

    for source, tables in schema.SOURCE_TABLES.items():
        for row in data.rows[tables["content"]]:
            keyword = row.get("source_keyword")
            scenario_key = keyword_to_scenario.get(keyword)
            if not scenario_key:
                continue
            biz_id = _content_biz_id(source, row)
            meta = data.content_index.get(biz_id, {})
            author_id = meta.get("author_id")
            influence = influence_by_account.get(author_id, 0.0)
            timelines[scenario_key].append({
                "ts": _content_time(source, row),
                "sentiment": meta.get("sentiment", SENTIMENT_NEUTRAL),
                "influence": influence,
                "is_bot": meta.get("is_bot", False),
                "text": (meta.get("text") or "")[:60],
            })
    for v in timelines.values():
        v.sort(key=lambda x: x["ts"])
    return timelines


# 烈度指数分项权重（对应 PRD FR-22：综合传播规模、负向强度、账号权重、敏感程度）
W_NEGATIVE = 0.50
W_VOLUME = 0.30
W_KOL = 0.12
W_SENSITIVE = 0.15
VOLUME_SATURATION = 40      # 声量归一化上限：单窗口 40 条以上视为传播规模满分

# 烈度分 -> 风险等级（对应 PRD FR-23）
LEVEL_THRESHOLDS = (("S", 0.72), ("A", 0.58), ("B", 0.40))


def _judge_risk(volume, negative_ratio, kol_involved, sensitive_hit):
    """按 PRD FR-22 / FR-23 的烈度指数思路推导风险等级，作为人工标注的替代。

    采用加权连续评分而非硬阈值组合，避免「8 条声量、75% 负向」这类
    小规模波动被误判为 S 级（极高危）——绝对声量阈值在小样本数据上尤其不可靠。
    返回 (等级, 判定依据)，依据含各分项得分，便于复核。
    """
    volume_score = min(volume / float(VOLUME_SATURATION), 1.0)
    intensity = (W_NEGATIVE * negative_ratio
                 + W_VOLUME * volume_score
                 + (W_KOL if kol_involved else 0.0)
                 + (W_SENSITIVE if sensitive_hit else 0.0))
    intensity = round(min(intensity, 1.0), 4)

    level = "C"
    for name, threshold in LEVEL_THRESHOLDS:
        if intensity >= threshold:
            level = name
            break

    factors = {
        "intensity_index": intensity,
        "negative_ratio": round(negative_ratio, 4),
        "volume": volume,
        "volume_score": round(volume_score, 4),
        "kol_involved": kol_involved,
        "sensitive_hit": sensitive_hit,
    }
    return level, factors


def build_risk_eval(data, rng, target=RISK_TARGET_TOTAL, window_minutes=60):
    """在时间线上滑窗聚合，生成事件级风险评测样本。"""
    timelines = _collect_scenario_timeline(data)
    scenario_by_key = {s.key: s for s in scenarios.SCENARIOS}
    candidates = []
    step = window_minutes // 2      # 窗口半重叠，保证候选数量充足

    for key, items in timelines.items():
        if not items:
            continue
        scenario = scenario_by_key[key]
        start = items[0]["ts"]
        end = items[-1]["ts"]
        window = window_minutes * 60
        cursor = start
        while cursor <= end:
            bucket = [i for i in items if cursor <= i["ts"] < cursor + window]
            if bucket:
                volume = len(bucket)
                negative = sum(1 for i in bucket if i["sentiment"] == SENTIMENT_NEGATIVE)
                negative_ratio = negative / volume
                kol_involved = any(i["influence"] >= 60 for i in bucket)
                # 敏感词命中：以场景是否为高敏感话题 + 负向措辞近似
                sensitive_hit = (scenario.key == "catering_food_safety"
                                 and negative_ratio >= 0.6)
                level, factors = _judge_risk(volume, negative_ratio,
                                             kol_involved, sensitive_hit)
                candidates.append({
                    "event_id": "",
                    "scenario": key,
                    "scenario_title": scenario.title,
                    "window_start": datetime.fromtimestamp(cursor, CST).strftime(
                        "%Y-%m-%d %H:%M:%S+08:00"),
                    "window_minutes": window_minutes,
                    "volume": volume,
                    "negative_ratio": round(negative_ratio, 4),
                    "kol_involved": kol_involved,
                    "sensitive_hit": sensitive_hit,
                    "event_desc": (
                        f"【{scenario.title}】{window_minutes} 分钟窗口内新增舆情 {volume} 条，"
                        f"负向占比 {negative_ratio:.0%}，"
                        f"{'有' if kol_involved else '无'}高权重账号参与，"
                        f"{'命中' if sensitive_hit else '未命中'}高危敏感话题"
                    ),
                    "gold_risk_level": level,
                    "gold_intensity_factors": factors,
                    "note": "由时间窗聚合指标按 PRD 附录 B.2 规则标注",
                })
            cursor += step * 60

    rng.shuffle(candidates)

    # 过滤掉烈度接近 0 的窗口：这类样本毫无区分度（闭眼选 C 就能对），
    # 混进来只会让评测集的分数虚高。仅当合格候选不足以凑满时予以放宽。
    MIN_INTENSITY = 0.15
    strong = [c for c in candidates
              if c["gold_intensity_factors"]["intensity_index"] >= MIN_INTENSITY]
    if len(strong) >= target:
        candidates = strong

    # 第一轮：每个「场景 × 风险等级」组合先取一条，保证 3 个场景 × 4 个等级都有覆盖，
    # 否则样本会被内容量最大的场景淹没
    picked = []
    picked_ids = set()
    seen_combo = set()
    for c in candidates:
        combo = (c["scenario"], c["gold_risk_level"])
        if combo in seen_combo:
            continue
        seen_combo.add(combo)
        picked.append(c)
        picked_ids.add(id(c))

    # 第二轮：按风险等级配额补足，等级均衡优先
    by_level = {}
    for c in candidates:
        if id(c) in picked_ids:
            continue
        by_level.setdefault(c["gold_risk_level"], []).append(c)

    per_level = max(1, (target - len(picked)) // max(1, len(by_level) or 1))
    for level in ("S", "A", "B", "C"):
        for c in by_level.get(level, [])[:per_level]:
            picked.append(c)
            picked_ids.add(id(c))

    # 第三轮：仍不足则按候选取用
    if len(picked) < target:
        for c in candidates:
            if len(picked) >= target:
                break
            if id(c) not in picked_ids:
                picked.append(c)
                picked_ids.add(id(c))

    picked = picked[:target]

    for i, c in enumerate(picked, start=1):
        c["event_id"] = f"re_{i:04d}"
    return picked


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def write_jsonl(records, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def build_all(data, rng, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    sentiment_records = build_sentiment_eval(data.samples, rng)
    risk_records = build_risk_eval(data, rng)
    paths = {
        "sentiment": write_jsonl(
            sentiment_records, os.path.join(out_dir, "sentiment_eval.jsonl")),
        "risk": write_jsonl(
            risk_records, os.path.join(out_dir, "risk_eval.jsonl")),
    }
    return sentiment_records, risk_records, paths
