"""数据生成核心：按场景剧本产出账号、内容与评论。

设计要点：
1. 每条生成记录都携带隐藏标签（情感 / 情绪 / 内容角色 / 所属场景），
   这些标签用于派生评测集，但不会写入数据库——数据库只存「看起来像爬来的」原始字段。
2. 互动量与账号权重、内容角色联动，保证「热度通道召回」有区分度：
   引爆节点的 KOL 内容比普通用户内容高 2~3 个数量级。
3. 评论通过业务 ID（note_id / aweme_id / article_id / thread_id）关联内容，
   与课程表结构保持一致，不是通过主键关联。
"""

import math
import random
from datetime import datetime, timedelta, timezone

import schema
import scenarios
from scenarios import (
    ACCOUNTS,
    BOT_DECORATIONS,
    BOT_TEMPLATES,
    COMMENT_LIBRARY,
    CONTENT_LIBRARY,
    IP_LOCATIONS,
    NICKNAME_PREFIXES,
    NICKNAME_SUFFIXES,
    ROLE_OFFICIAL,
    ROLE_PROFILE,
    SENTIMENT_NEGATIVE,
    SENTIMENT_NEUTRAL,
    SENTIMENT_POSITIVE,
    SCENARIOS,
)

CST = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------
# 规模档位
# ---------------------------------------------------------------------------

SCALE_PROFILES = {
    "small": {
        "content_total": 500,
        "derived_accounts_per_source": 90,
        "comment_scale": 1.0,
    },
    "prd": {
        "content_total": 10000,
        "derived_accounts_per_source": 1200,
        "comment_scale": 0.72,
    },
}

# 官方回应类内容的评论情绪分布另设：以观望与认可为主
OFFICIAL_COMMENT_MIX = {"neutral": 0.62, "positive": 0.26, "negative": 0.12}

# ---------------------------------------------------------------------------
# 文本装饰素材
#
# 为什么需要它：模板库只有几十条文案，却要生成上千条评论。不做组合，
# 会出现大量**逐字相同**的内容——SimHash 会正确地把它们全部合并，
# 结果是「接入 2500 条、去重后只剩 270 条」，声量与情感分布彻底失真。
#
# 装饰的意义不是"加噪声"，而是逼近真实语料的表达多样性：
# 同一件事，有人直说、有人先铺垫语气、有人末尾补一句态度。
# 空串占比较高，是为了让大部分评论保持朴素表达，不至于每条都带口头禅。
# ---------------------------------------------------------------------------

CONTENT_PREFIXES = [
    "", "", "", "", "",
    "分享一下：", "说说我的看法：", "记录一下：", "转需：", "个人经历：",
    "说个情况：", "反馈一下：", "不吐不快：", "客观说：",
]

CONTENT_SUFFIXES = [
    "", "", "", "", "",
    " 大家怎么看？", " 有同样情况的吗？", " 坐等后续。",
    " 有没有懂的说一下", " 已反馈给客服",
]

COMMENT_PREFIXES = [
    "", "", "", "", "",
    "说实话，", "讲真，", "我就想说，", "客观讲，", "纯路人，",
    "不是，", "怎么讲呢，", "反正我，",
]

# ⚠️ 装饰词必须是**语义中性**的：只做语气铺垫，不表达情感倾向。
#
# 这里踩过坑：曾经把「无语」「服了」「唉」「真的会谢」放进后缀池，
# 于是「同价位还有什么可以选的」被装饰成「同价位还有什么可以选的 无语」——
# 文本实际已变成负向，但生成评测集时仍按原始模板标成中性，
# 造成**标注与文本不符**，直接拉低评测准确率（简单样本反而比困难样本低 18 个点）。
# 去掉带倾向的词之后，装饰只增加表达多样性，不改变语义。
COMMENT_SUFFIXES = [
    "", "", "", "", "",
    " 顶", " +1", " 同感", " 你们怎么看", " 转发扩散",
    " 蹲一个后续", " 已反馈", " 求扩散",
]

SCENARIO_TOPIC_TAGS = {
    "consumer_electronics": ["#星耀XPhone15#", "#数码#", "#手机发热#", "#新机体验#"],
    "catering_food_safety": ["#味鲜居后厨#", "#食品安全#", "#探店#", "#消费者权益#"],
    "policy_benefit": ["#新能源车购置税#", "#汽车产业#", "#购置税减免#", "#新能源#"],
}

# 论坛 / 新闻评论用户与社交平台用户共用一个派生账号池，此处只控制昵称风格
AVATAR_TEMPLATE = "https://avatar.example-{cdn}.com/{kind}/{uid}.jpg"
AVATAR_CDNS = ["p1", "p2", "p3", "p4"]
AVATAR_KINDS = ["user", "media", "avatar", "profile"]


class GeneratedData:
    """一次生成的完整产物。"""

    def __init__(self):
        self.accounts = []                     # platform_account 行
        self.rows = {t: [] for t in schema.TABLE_ORDER}
        self.samples = []                      # 带隐藏标签的文本样本（供评测集）
        self.content_index = {}                # 业务ID -> 内容元信息（供关联与自检）
        self.stats = {}

    def add_sample(self, text, sentiment, emotion, source_type, scenario,
                   role, difficulty="normal"):
        self.samples.append({
            "text": text,
            "sentiment": sentiment,
            "emotion": emotion,
            "source_type": source_type,
            "scenario": scenario,
            "role": role,
            "difficulty": difficulty,
        })


class _IdGen:
    """业务 ID 生成器：带随机起点的**严格递增**序列。

    必须严格递增，不能靠"随机间隔"制造自然感。早期实现写成
    `base + offset * randint(1, 7)`，看似合理，实际上 `1×2` 与 `2×1` 会算出同一个值——
    ID 重复后，评论会按业务 ID 关联到**错误的内容**上，
    而且整个链路不会报任何错，只是统计结果悄悄失真。
    """

    def __init__(self, rng, start, width=16, step=7):
        self._rng = rng
        self._base = rng.randint(10 ** (width - 1), 10 ** width - 10 ** (width - 1))
        self._offset = 0
        self._step = step

    def next(self) -> int:
        # 每个 offset 占据长度为 step 的互不重叠区间，区间内仍保留随机性
        self._offset += 1
        return (self._base + self._offset * self._step
                + self._rng.randint(0, self._step - 1))


class Builder:
    def __init__(self, seed=42, end_time=None, scale="small"):
        if scale not in SCALE_PROFILES:
            raise ValueError(f"未知规模档位：{scale}")
        self.scale_name = scale
        self.profile = SCALE_PROFILES[scale]
        self.rng = random.Random(seed)
        self.seed = seed
        self.end_time = end_time or datetime.now(CST).replace(microsecond=0)
        if self.end_time.tzinfo is None:
            self.end_time = self.end_time.replace(tzinfo=CST)

        self.ids = {
            "note": _IdGen(self.rng, 5300000000000000, 16),
            "aweme": _IdGen(self.rng, 7648000000000000000, 19),
            "thread": _IdGen(self.rng, 8000000, 8),
            "comment": _IdGen(self.rng, 7000000000000000, 16),
            "post": _IdGen(self.rng, 900000000, 10),
        }
        self.data = GeneratedData()
        self._derived_accounts = {}     # source -> [account dict]
        self._account_rows = {}         # account_id -> row dict
        self._row_index = {}            # (table, 业务键) -> 行对象，避免 O(n) 回查
        self._floor_counter = {}        # thread_id -> 当前楼层数

    # ------------------------------------------------------------------
    # 账号
    # ------------------------------------------------------------------

    def _make_account_row(self, account_id, platform, nickname, follower_count,
                          verified_type, ip_location, is_bot=0):
        influence = scenarios.calc_influence(follower_count, verified_type)
        return {
            "id": len(self.data.accounts) + 1,
            "account_id": account_id,
            "platform": schema.SOURCE_LABELS.get(platform, platform),
            "nickname": nickname,
            "avatar": AVATAR_TEMPLATE.format(
                cdn=self.rng.choice(AVATAR_CDNS),
                kind=self.rng.choice(AVATAR_KINDS),
                uid=account_id),
            "follower_count": follower_count,
            "verified_type": verified_type,
            "influence_score": influence,
            "ip_location": ip_location or self.rng.choice(IP_LOCATIONS),
            "is_suspected_bot": is_bot,
            "add_ts": self._ts(self.rng.randint(-7200, -60)),
        }, influence

    def _register_account(self, row, influence):
        self.data.accounts.append(row)
        self._account_rows[row["account_id"]] = dict(row, _influence=influence)

    def _build_accounts(self):
        """登记具名账号，并按信源派生散户账号。"""
        for acc in ACCOUNTS:
            row, influence = self._make_account_row(
                acc.key, acc.platform, acc.nickname, acc.follower_count,
                acc.verified_type, acc.ip_location, acc.is_bot)
            self._register_account(row, influence)

        n = self.profile["derived_accounts_per_source"]
        for source in (schema.SOURCE_SOCIAL, schema.SOURCE_VIDEO,
                       schema.SOURCE_NEWS, schema.SOURCE_FORUM):
            pool = []
            for _ in range(n):
                # 粉丝量长尾分布：多数为几十到几百的普通用户
                follower = int(math.exp(self.rng.gauss(4.2, 1.5)))
                follower = max(1, min(follower, 60000))
                nickname = (self.rng.choice(NICKNAME_PREFIXES)
                            + self.rng.choice(NICKNAME_SUFFIXES)
                            + (str(self.rng.randint(1, 999)) if self.rng.random() < 0.35 else ""))
                account_id = "u" + "".join(str(self.rng.randint(0, 9)) for _ in range(12))
                row, influence = self._make_account_row(
                    account_id, source, nickname, follower,
                    "普通用户", self.rng.choice(IP_LOCATIONS))
                self._register_account(row, influence)
                pool.append(account_id)
            self._derived_accounts[source] = pool

    def _pick_author(self, source, author_keys):
        """按 batch 指定选作者，支持 'derived' 与 'bot_pool' 两个特殊标记。"""
        if "bot_pool" in author_keys:
            bots = [a.key for a in ACCOUNTS if a.is_bot and a.platform == source]
            if not bots:
                bots = [a.key for a in ACCOUNTS if a.is_bot]
            return self.rng.choice(bots)
        if "derived" in author_keys or not author_keys:
            pool = self._derived_accounts.get(source) or self._derived_accounts[schema.SOURCE_SOCIAL]
            return self.rng.choice(pool)
        return self.rng.choice(author_keys)

    # ------------------------------------------------------------------
    # 时间与互动量
    # ------------------------------------------------------------------

    def _ts(self, offset_min):
        dt = self.end_time + timedelta(minutes=offset_min)
        return int(dt.timestamp())

    def _dt_str(self, ts):
        return datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d %H:%M:%S+08:00")

    def _engage(self, role, account_id, extra=1.0):
        """互动量 = 角色基准 × 账号影响因子 × 随机抖动。"""
        base = ROLE_PROFILE[role]["base_engage"]
        influence = self._account_rows[account_id]["_influence"]
        # 影响因子：0.35 ~ 3.0，使高权重账号的内容热度显著更高
        factor = 0.35 + (influence / 100.0) * 2.6
        jitter = self.rng.lognormvariate(0, 0.55)
        return max(1, int(base * factor * jitter * extra))

    # ------------------------------------------------------------------
    # 文本
    # ------------------------------------------------------------------

    def _pick_text_template(self, scenario_key, role, sentiment):
        lib = CONTENT_LIBRARY[scenario_key].get(role) or []
        matches = [g for g in lib if g["sentiment"] == sentiment]
        if not matches:
            matches = lib
        if not matches:
            # 该角色在本场景没有模板（不应发生），退回任意可用模板
            for role_lib in CONTENT_LIBRARY[scenario_key].values():
                if role_lib:
                    matches = role_lib
                    break
        return self.rng.choice(matches)

    def _decorate_content(self, text, scenario_key):
        prefix = self.rng.choice(CONTENT_PREFIXES)
        suffix = self.rng.choice(CONTENT_SUFFIXES)
        out = prefix + text + suffix
        if self.rng.random() < 0.35:
            out += " " + self.rng.choice(SCENARIO_TOPIC_TAGS[scenario_key])
        return out

    def _decorate_comment(self, text, scenario_key):
        """给评论文本加前缀/后缀，制造表达层面的多样性。

        前缀与后缀各自独立随机，组合数约 13 × 18 = 234 种。
        没有这一步，同一情绪类别下只有几条模板文案被反复抽取，
        去重会把它们全部合并掉。
        """
        prefix = self.rng.choice(COMMENT_PREFIXES)
        suffix = self.rng.choice(COMMENT_SUFFIXES)
        return f"{prefix}{text}{suffix}"

    def _pick_sentiment(self, mix):
        keys = list(mix.keys())
        weights = [mix[k] for k in keys]
        return self.rng.choices(keys, weights=weights, k=1)[0]

    def _pick_comment_text(self, scenario_key, sentiment, emotion=None):
        lib = COMMENT_LIBRARY.get(scenario_key, {}).get(sentiment, {})
        if not lib:
            # 该场景没有该情感的评论文案，退回中性
            lib = COMMENT_LIBRARY.get(scenario_key, {}).get(SENTIMENT_NEUTRAL, {})
        if emotion and emotion in lib:
            candidates = lib[emotion]
        else:
            candidates = [t for texts in lib.values() for t in texts] or ["……"]
        return self.rng.choice(candidates)

    def _pick_emotion(self, scenario_key, sentiment):
        lib = COMMENT_LIBRARY.get(scenario_key, {}).get(sentiment, {})
        if not lib:
            return ""
        return self.rng.choice(list(lib.keys()))

    def _bot_text(self, scenario_key):
        base = self.rng.choice(BOT_TEMPLATES.get(scenario_key, ["太过分了"]))
        prefix, suffix = self.rng.choice(BOT_DECORATIONS)
        return prefix + base + suffix

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def build(self):
        self._build_accounts()

        total = self.profile["content_total"]
        grand_weight = sum(s.total_weight for s in SCENARIOS)

        for scenario in SCENARIOS:
            scenario_total = max(10, round(total * scenario.total_weight / grand_weight))
            for batch in scenario.batches:
                count = max(1, round(scenario_total * batch.weight / scenario.total_weight))
                # repeat 专供水军批次：它们的内容量靠「同一批账号反复发」堆出来，
                # 而不是靠权重——权重调大会挤占正常内容的配额
                count *= max(1, batch.repeat)
                self._emit_batch(scenario, batch, count)

        self._finalize_stats()
        return self.data

    def _bot_accounts(self, source) -> list[str]:
        """取该信源可用的水军账号。"""
        bots = [a.key for a in ACCOUNTS if a.is_bot and a.platform == source]
        return bots or [a.key for a in ACCOUNTS if a.is_bot]

    def _emit_batch(self, scenario, batch, count):
        is_bot_batch = "bot_pool" in batch.authors
        # 水军账号**轮转分配**而非随机抽取：真实刷量是同一批账号集中发，
        # 随机分配会让每个账号只摊到一两条，达不到「短时间窗内多条雷同」的特征，
        # 水军识别自然也就无从触发
        bot_accounts = self._bot_accounts(batch.source) if is_bot_batch else []

        for index in range(count):
            if is_bot_batch and bot_accounts:
                author_id = bot_accounts[index % len(bot_accounts)]
            else:
                author_id = self._pick_author(batch.source, batch.authors)
            sentiment = self._pick_sentiment(batch.sentiment_mix)

            if is_bot_batch:
                text = self._bot_text(scenario.key)
                # 刷量内容一律负向 —— 水军的作用就是把负面声音刷上去
                template_group = {"sentiment": SENTIMENT_NEGATIVE,
                                  "emotion": scenarios.EMOTION_ANGER, "title": ""}
            else:
                template_group = self._pick_text_template(scenario.key, batch.role, sentiment)
                raw = self.rng.choice(template_group["texts"])
                text = self._decorate_content(raw, scenario.key)

            offset = batch.offset_min + self.rng.uniform(0, batch.spread_min)
            # 截断到时间锚点：批次的抖动范围可能越过结束时间
            content_ts = min(self._ts(offset), int(self.end_time.timestamp()))
            engage = self._engage(batch.role, author_id, extra=(0.6 if is_bot_batch else 1.0))

            content_meta = {
                "scenario": scenario.key,
                "role": batch.role,
                # 情感标签**以模板实际情感为准**，而不是抽样值。
                # 当某个角色下没有该情感的模板时，_pick_text_template 会退回全部模板——
                # 此时若仍用抽样值当标签，就会出现「文本是负向、标签是中性」的错标，
                # 评测时把模型的正确判断当成错误。
                "sentiment": template_group["sentiment"],
                "emotion": template_group.get("emotion", ""),
                "source_type": batch.source,
                "author_id": author_id,
                "create_time": content_ts,
                "text": text,
                "title": template_group.get("title", ""),
                "is_bot": is_bot_batch,
            }
            biz_id = self._write_content(scenario, batch, content_meta, engage)
            content_meta["biz_id"] = biz_id
            self.data.content_index[biz_id] = content_meta

            # 标签必须与**实际写入的文本**一致：这里同样用 template_group 的情感，
            # 而不是 batch.sentiment_mix 的抽样值。两处（落库与样本采样）
            # 必须同源，否则评测集会出现「文本是负向、gold 是中性」的错标。
            self.data.add_sample(text, template_group["sentiment"],
                                 template_group.get("emotion", ""),
                                 batch.source, scenario.key, batch.role)

            if not is_bot_batch:
                self._emit_comments(scenario, batch, content_meta, engage)

    # ------------------------------------------------------------------
    # 内容表写入
    # ------------------------------------------------------------------

    def _write_content(self, scenario, batch, meta, engage):
        ts = meta["create_time"]
        author = self._account_rows[meta["author_id"]]
        source = batch.source
        text = meta["text"]
        title = meta["title"] or text[:28]
        keyword = scenario.keyword
        source_label = schema.SOURCE_LABELS[source]

        if source == schema.SOURCE_SOCIAL:
            biz_id = self.ids["note"].next()
            row = {
                "id": len(self.data.rows["weibo_note"]) + 1,
                "user_id": author["account_id"],
                "nickname": author["nickname"],
                "avatar": author["avatar"],
                "gender": self.rng.choice(["m", "f"]),
                "profile_url": f"https://m.weibo.cn/u/{str(author['account_id']).lstrip('u')}",
                "ip_location": author["ip_location"],
                "add_ts": ts + self.rng.randint(30, 600),
                "last_modify_ts": ts + self.rng.randint(600, 3600),
                "note_id": biz_id,
                "content": text,
                "create_time": ts,
                "create_date_time": self._dt_str(ts),
                "liked_count": str(engage),
                "comments_count": "0",       # 生成评论后回填
                "shared_count": str(int(engage * self.rng.uniform(0.05, 0.4))),
                "note_url": f"https://m.weibo.cn/detail/{biz_id}",
                "source_keyword": keyword,
            }
            self.data.rows["weibo_note"].append(row)
            self._row_index[("weibo_note", biz_id)] = row

        elif source == schema.SOURCE_VIDEO:
            biz_id = self.ids["aweme"].next()
            row = {
                "id": len(self.data.rows["douyin_aweme"]) + 1,
                "user_id": author["account_id"],
                "sec_uid": "MS4wLjABAAAA" + "".join(
                    self.rng.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
                    for _ in range(43)),
                "short_user_id": str(self.rng.randint(10 ** 9, 10 ** 11 - 1)),
                "user_unique_id": "dy" + str(self.rng.randint(10 ** 9, 10 ** 12 - 1)),
                "nickname": author["nickname"],
                "avatar": author["avatar"],
                "user_signature": self.rng.choice(["", "记录生活", "分享日常", "关注我了解更多"]),
                "ip_location": author["ip_location"],
                "add_ts": ts + self.rng.randint(30, 600),
                "last_modify_ts": ts + self.rng.randint(600, 3600),
                "aweme_id": biz_id,
                "aweme_type": "0",
                "title": title,
                "desc": text,
                "create_time": ts,
                "liked_count": str(int(engage * self.rng.uniform(1.0, 2.5))),
                "comment_count": "0",
                "share_count": str(int(engage * self.rng.uniform(0.1, 0.9))),
                "collected_count": str(int(engage * self.rng.uniform(0.2, 1.2))),
                "aweme_url": f"https://www.douyin.com/video/{biz_id}",
                "cover_url": f"https://cover.example-cdn.com/{biz_id}.jpeg",
                "video_download_url": "",
                "music_download_url": "",
                "note_download_url": "",
                "source_keyword": keyword,
            }
            self.data.rows["douyin_aweme"].append(row)
            self._row_index[("douyin_aweme", biz_id)] = row

        elif source == schema.SOURCE_NEWS:
            biz_id = len(self.data.rows["news_article"]) + 1
            media = author["nickname"]
            row = {
                "id": biz_id,
                "media_name": media,
                "author": self.rng.choice(["本报记者", "综合报道", "本报讯", "记者 王", "记者 李"]),
                "title": title,
                "content": text,
                "publish_time": ts,
                "publish_date_time": self._dt_str(ts),
                "url": f"https://news.example-portal.com/{source_label}/{biz_id}.html",
                "read_count": str(engage * self.rng.randint(4, 20)),
                "comment_count": "0",
                "share_count": str(int(engage * self.rng.uniform(0.05, 0.5))),
                "source_keyword": keyword,
            }
            self.data.rows["news_article"].append(row)
            self._row_index[("news_article", biz_id)] = row

        else:  # 行业论坛
            biz_id = self.ids["thread"].next()
            row = {
                "id": len(self.data.rows["forum_thread"]) + 1,
                "thread_id": biz_id,
                "forum_name": self.rng.choice(["数码之家", "车友论坛", "餐饮观察", "行业交流区"]),
                "board": self.rng.choice(["讨论区", "技术版", "曝光台", "经验分享"]),
                "author_id": author["account_id"],
                "author_nickname": author["nickname"],
                "title": title,
                "content": text,
                "publish_time": ts,
                "publish_date_time": self._dt_str(ts),
                "view_count": str(engage * self.rng.randint(8, 40)),
                "reply_count": "0",
                "is_pinned": 0,
                "is_essence": 1 if batch.role == scenarios.ROLE_ANALYSIS else 0,
                "url": f"https://bbs.example-forum.com/thread/{biz_id}.html",
                "source_keyword": keyword,
            }
            self.data.rows["forum_thread"].append(row)
            self._row_index[("forum_thread", biz_id)] = row

        return biz_id

    # ------------------------------------------------------------------
    # 评论
    # ------------------------------------------------------------------

    def _emit_comments(self, scenario, batch, content_meta, engage):
        source = batch.source
        lo, hi = ROLE_PROFILE[batch.role]["comments"]
        raw_count = self.rng.randint(lo, hi)
        count = max(1, int(raw_count * self.profile["comment_scale"]))

        if batch.role == ROLE_OFFICIAL:
            mix = OFFICIAL_COMMENT_MIX
        else:
            mix = scenario.sentiment_expect

        comment_ids = []
        posted = []
        for i in range(count):
            sentiment = self._pick_sentiment(mix)
            emotion = self._pick_emotion(scenario.key, sentiment)
            text = self._decorate_comment(
                self._pick_comment_text(scenario.key, sentiment, emotion), scenario.key)

            author_id = self._pick_author(source, ["derived"])
            author = self._account_rows[author_id]
            # 评论时间晚于内容，并在时间窗内随序号均匀铺开（模拟陆续涌入）
            span = max(300, min(6 * 3600, int(engage / 8) + 600))
            offset = (content_meta["create_time"]
                      + int(span * (i + 1) / (count + 1))
                      + self.rng.randint(-45, 45))
            offset = min(offset, int(self.end_time.timestamp()))

            parent = ""
            if posted and self.rng.random() < 0.16:
                parent = str(self.rng.choice(posted))

            biz_id = self._write_comment(source, scenario, content_meta, author,
                                         text, offset, engage, parent)
            comment_ids.append(biz_id)
            posted.append(biz_id)

            self.data.add_sample(text, sentiment, emotion, source,
                                 scenario.key, "comment")

        # 回填内容的评论数：数据库记录的是平台总评论数，
        # 实际抓取的只是样本，因此放大若干倍更贴近真实形态
        self._backfill_comment_count(source, content_meta["biz_id"], len(comment_ids), engage)
        return comment_ids

    def _write_comment(self, source, scenario, content_meta, author, text, ts, engage, parent):
        like = max(0, int(engage * self.rng.uniform(0.0005, 0.02)))
        biz_id = self.ids["comment"].next()

        if source == schema.SOURCE_SOCIAL:
            row = {
                "id": len(self.data.rows["weibo_note_comment"]) + 1,
                "user_id": author["account_id"],
                "nickname": author["nickname"],
                "avatar": author["avatar"],
                "gender": self.rng.choice(["m", "f"]),
                "profile_url": f"https://m.weibo.cn/u/{str(author['account_id']).lstrip('u')}",
                "ip_location": author["ip_location"],
                "add_ts": ts + self.rng.randint(60, 3600),
                "last_modify_ts": ts + self.rng.randint(60, 3600),
                "comment_id": biz_id,
                "note_id": content_meta["biz_id"],
                "content": text,
                "create_time": ts,
                "create_date_time": self._dt_str(ts),
                "comment_like_count": str(like),
                "sub_comment_count": "0",
                "parent_comment_id": parent,
            }
            self.data.rows["weibo_note_comment"].append(row)

        elif source == schema.SOURCE_VIDEO:
            row = {
                "id": len(self.data.rows["douyin_aweme_comment"]) + 1,
                "user_id": author["account_id"],
                "sec_uid": "MS4wLjABAAAA" + "".join(
                    self.rng.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
                    for _ in range(43)),
                "short_user_id": str(self.rng.randint(10 ** 9, 10 ** 11 - 1)),
                "user_unique_id": "dy" + str(self.rng.randint(10 ** 9, 10 ** 12 - 1)),
                "nickname": author["nickname"],
                "avatar": author["avatar"],
                "user_signature": "",
                "ip_location": author["ip_location"],
                "add_ts": ts + self.rng.randint(60, 3600),
                "last_modify_ts": ts + self.rng.randint(60, 3600),
                "comment_id": biz_id,
                "aweme_id": content_meta["biz_id"],
                "content": text,
                "create_time": ts,
                "sub_comment_count": "0",
                "parent_comment_id": parent,
                "like_count": str(like),
                "pictures": "",
            }
            self.data.rows["douyin_aweme_comment"].append(row)

        elif source == schema.SOURCE_NEWS:
            row = {
                "id": len(self.data.rows["news_article_comment"]) + 1,
                "user_id": author["account_id"],
                "nickname": author["nickname"],
                "avatar": author["avatar"],
                "ip_location": author["ip_location"],
                "comment_id": biz_id,
                "article_id": content_meta["biz_id"],
                "content": text,
                "create_time": ts,
                "create_date_time": self._dt_str(ts),
                "comment_like_count": str(like),
                "sub_comment_count": "0",
                "parent_comment_id": parent,
            }
            self.data.rows["news_article_comment"].append(row)

        else:  # 论坛回帖
            biz_id = self.ids["post"].next()
            # 1 楼是主帖，回帖自 2 楼起，用计数器避免遍历全表
            thread_key = content_meta["biz_id"]
            floor = self._floor_counter.get(thread_key, 1) + 1
            self._floor_counter[thread_key] = floor
            row = {
                "id": len(self.data.rows["forum_post"]) + 1,
                "post_id": biz_id,
                "thread_id": content_meta["biz_id"],
                "author_id": author["account_id"],
                "author_nickname": author["nickname"],
                "content": text,
                "create_time": ts,
                "create_date_time": self._dt_str(ts),
                "like_count": str(like),
                "floor_no": floor,
                "reply_to_post_id": parent,
                "is_op": 0,
            }
            self.data.rows["forum_post"].append(row)

        return biz_id

    def _backfill_comment_count(self, source, biz_id, actual_count, engage):
        """回填内容表的评论总数（按抓取样本放大，贴近真实平台数据形态）。"""
        display = str(max(actual_count, int(actual_count * self.rng.uniform(2.0, 7.0))))
        table, column = {
            schema.SOURCE_SOCIAL: ("weibo_note", "comments_count"),
            schema.SOURCE_VIDEO: ("douyin_aweme", "comment_count"),
            schema.SOURCE_NEWS: ("news_article", "comment_count"),
            schema.SOURCE_FORUM: ("forum_thread", "reply_count"),
        }[source]
        row = self._row_index.get((table, biz_id))
        if row is not None:
            row[column] = display

    # ------------------------------------------------------------------
    # 统计（供自检与 README 使用）
    # ------------------------------------------------------------------

    def _finalize_stats(self):
        self.data.stats_end_time = self.end_time
        by_source = {}
        for source, tables in schema.SOURCE_TABLES.items():
            content_n = len(self.data.rows[tables["content"]])
            comment_n = len(self.data.rows[tables["comment"]])
            by_source[source] = {"content": content_n, "comment": comment_n}

        sentiment_counts = {SENTIMENT_POSITIVE: 0, SENTIMENT_NEUTRAL: 0, SENTIMENT_NEGATIVE: 0}
        scenario_counts = {}
        for s in self.data.samples:
            if s["role"] == "comment":
                sentiment_counts[s["sentiment"]] = sentiment_counts.get(s["sentiment"], 0) + 1
            scenario_counts[s["scenario"]] = scenario_counts.get(s["scenario"], 0) + 1

        total_comment = sum(sentiment_counts.values()) or 1
        self.data.stats = {
            "scale": self.scale_name,
            "seed": self.seed,
            "end_time": self.end_time.strftime("%Y-%m-%d %H:%M:%S%z"),
            "accounts": len(self.data.accounts),
            "content_total": sum(v["content"] for v in by_source.values()),
            "comment_total": sum(v["comment"] for v in by_source.values()),
            "by_source": by_source,
            "comment_sentiment": {
                k: round(v / total_comment, 4) for k, v in sentiment_counts.items()
            },
            "by_scenario": scenario_counts,
        }
