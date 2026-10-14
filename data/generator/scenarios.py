"""场景剧本与文案素材库。

模拟数据不是随机文本，每个场景内置一条有因果、有时间线、有情绪演变的剧情。
这是让「突发预警」「传播溯源」「观点聚类」三个演示能真实跑通的前提——
随机生成的数据在这三条链路上都得不出有意义的结论。

三个场景：
  1. consumer_electronics  消费电子质量争议（S 级，主导信源：社交媒体 + 行业论坛）
  2. catering_food_safety  连锁餐饮食品安全（A/S 级，主导信源：短视频）
  3. policy_benefit        行业政策利好（C 级，主导信源：新闻门户 + 行业论坛）—— 正向对照场景

所有品牌、产品、机构、媒体、账号均为虚构，避免影射真实主体。
"""

import math

from schema import (
    SOURCE_SOCIAL,
    SOURCE_VIDEO,
    SOURCE_NEWS,
    SOURCE_FORUM,
)

# ---------------------------------------------------------------------------
# 内容角色：决定互动量量级、评论密度与在传播链中的位置
# ---------------------------------------------------------------------------

ROLE_ORIGIN = "origin"          # 首发节点
ROLE_IGNITION = "ignition"      # 引爆节点（KOL / 高权重）
ROLE_SPREAD = "spread"          # 扩散节点
ROLE_ANALYSIS = "analysis"      # 深度分析（论坛长帖 / 技术帖）
ROLE_FOLLOWUP = "followup"      # 媒体跟进
ROLE_OFFICIAL = "official"      # 官方回应
ROLE_AMBIENT = "ambient"        # 背景讨论（与事件弱相关，用于稀释噪声）

# 角色 -> (互动量基准, 评论条数区间)
ROLE_PROFILE = {
    ROLE_ORIGIN:    {"base_engage": 120,     "comments": (4, 10)},
    ROLE_IGNITION:  {"base_engage": 180000,  "comments": (18, 40)},
    ROLE_SPREAD:    {"base_engage": 2600,    "comments": (6, 16)},
    ROLE_ANALYSIS:  {"base_engage": 48000,   "comments": (10, 24)},
    ROLE_FOLLOWUP:  {"base_engage": 86000,   "comments": (8, 20)},
    ROLE_OFFICIAL:  {"base_engage": 32000,   "comments": (12, 28)},
    ROLE_AMBIENT:   {"base_engage": 60,      "comments": (1, 4)},
}

# 情绪标签（与 PRD 附录 B.2 一致）
SENTIMENT_POSITIVE = "positive"
SENTIMENT_NEUTRAL = "neutral"
SENTIMENT_NEGATIVE = "negative"

EMOTION_ANGER = "愤怒"
EMOTION_DISAPPOINTMENT = "失望"
EMOTION_PANIC = "恐慌"
EMOTION_DOUBT = "质疑"
EMOTION_NONE = ""            # 正向 / 中性不细分标签

# ---------------------------------------------------------------------------
# 账号池
# ---------------------------------------------------------------------------

class Account:
    """账号定义。influence_score 由粉丝量与认证类型推导，供 FR-29 账号权重评估使用。"""

    def __init__(self, key, platform, nickname, follower_count,
                 verified_type="普通用户", ip_location="", is_bot=0):
        self.key = key
        self.platform = platform
        self.nickname = nickname
        self.follower_count = follower_count
        self.verified_type = verified_type
        self.ip_location = ip_location
        self.is_bot = is_bot
        self.influence_score = calc_influence(follower_count, verified_type)


def calc_influence(follower_count, verified_type):
    """影响力评分：粉丝量取对数为主，认证类型加权。结果落在 0-100。"""
    base = math.log10(max(follower_count, 1) + 1) * 12.5          # 0~75 左右
    bonus = {"普通用户": 0.0, "黄V": 8.0, "蓝V": 10.0,
             "媒体": 14.0, "官方": 16.0, "政务": 18.0}.get(verified_type, 0.0)
    return round(min(base + bonus, 100.0), 2)


ACCOUNTS = [
    # ---- 场景 1：消费电子 ----
    Account("kol_tech_zhang", SOURCE_SOCIAL, "科技老张", 862000, "黄V", "北京"),
    Account("kol_digital_lab", SOURCE_VIDEO, "数码实验室", 1340000, "黄V", "广东"),
    Account("kol_chip_insider", SOURCE_FORUM, "芯片内行", 41000, "普通用户", "上海"),
    Account("brand_xingyao", SOURCE_SOCIAL, "星耀科技", 2680000, "官方", "广东"),
    Account("brand_xingyao_cs", SOURCE_SOCIAL, "星耀客服中心", 340000, "蓝V", "广东"),
    Account("media_kejichuanzhen", SOURCE_NEWS, "科技传真", 0, "媒体", "北京"),
    Account("media_xiaofei_wanbao", SOURCE_NEWS, "消费晚报", 0, "媒体", "上海"),
    Account("forum_mod_digital", SOURCE_FORUM, "数码区版主", 0, "蓝V", "江苏"),

    # ---- 场景 2：连锁餐饮 ----
    Account("user_food_hunter", SOURCE_VIDEO, "探店小分队", 76000, "普通用户", "四川"),
    Account("kol_eat_local", SOURCE_VIDEO, "本地吃喝玩乐", 520000, "黄V", "四川"),
    Account("gov_food_safety", SOURCE_SOCIAL, "市监之声", 890000, "政务", "四川"),
    Account("brand_weixianju", SOURCE_SOCIAL, "味鲜居餐饮", 1200000, "官方", "四川"),
    Account("media_city_news", SOURCE_NEWS, "城市早报", 0, "媒体", "四川"),
    Account("media_health_weekly", SOURCE_NEWS, "健康周刊", 0, "媒体", "北京"),

    # ---- 场景 3：行业政策 ----
    Account("media_finance_daily", SOURCE_NEWS, "财经日报", 0, "媒体", "北京"),
    Account("media_industry_observer", SOURCE_NEWS, "产业观察网", 0, "媒体", "上海"),
    Account("gov_industry_dept", SOURCE_NEWS, "行业主管部门", 0, "政务", "北京"),
    Account("forum_auto_veteran", SOURCE_FORUM, "老车主说车", 0, "普通用户", "山东"),
    Account("kol_auto_reviewer", SOURCE_SOCIAL, "汽车测评人老王", 640000, "黄V", "山东"),

    # ---- 水军账号（用于 SimHash 去重与刷量识别演示）----
    Account("bot_001", SOURCE_SOCIAL, "用户8837291", 12, "普通用户", "未知", is_bot=1),
    Account("bot_002", SOURCE_SOCIAL, "用户4410528", 8, "普通用户", "未知", is_bot=1),
    Account("bot_003", SOURCE_SOCIAL, "用户7763110", 5, "普通用户", "未知", is_bot=1),
    Account("bot_004", SOURCE_VIDEO, "用户2209843", 15, "普通用户", "未知", is_bot=1),
    Account("bot_005", SOURCE_VIDEO, "用户9104776", 3, "普通用户", "未知", is_bot=1),
    Account("bot_006", SOURCE_SOCIAL, "用户3390215", 6, "普通用户", "未知", is_bot=1),
]

ACCOUNT_BY_KEY = {a.key: a for a in ACCOUNTS}

# 普通用户的昵称素材，生成器据此派生大量散户账号
NICKNAME_PREFIXES = [
    "小", "老", "阿", "大", "迷路的", "爱吃的", "晚风", "山间", "海边的",
    "不吃香菜的", "加班的", "摸鱼的", "追风的", "沉默的", "认真的",
    "柠檬", "橘子", "栗子", "番茄", "抹茶", "乌龙", "冻梨",
]
NICKNAME_SUFFIXES = [
    "同学", "路人", "日记", "观察", "日记本", "碎碎念", "打卡", "随便说说",
    "不困", "想睡觉", "在摸鱼", "要努力", "有点烦", "很快乐", "在发呆",
]
IP_LOCATIONS = [
    "北京", "上海", "广东", "江苏", "浙江", "四川", "山东", "河南",
    "湖北", "湖南", "福建", "陕西", "重庆", "天津", "安徽", "辽宁", "云南",
]

# ---------------------------------------------------------------------------
# 场景定义
# ---------------------------------------------------------------------------

class Batch:
    """一批内容：在时间线某一点附近，由某类账号在某个信源上发布。

    offset_min  相对「结束时间」的分钟偏移（负值 = 过去）
    spread_min  批内时间抖动范围
    weight      在内容总量中的分配权重
    sentiment_mix  情感配比，如 {"negative": 0.8, "neutral": 0.2}
    """

    def __init__(self, offset_min, source, role, authors, sentiment_mix,
                 weight=1.0, spread_min=30, topic_keys=None, repeat=1):
        self.offset_min = offset_min
        self.source = source
        self.role = role
        self.authors = authors
        self.sentiment_mix = sentiment_mix
        self.weight = weight
        self.spread_min = spread_min
        self.topic_keys = topic_keys or []
        self.repeat = repeat


class Scenario:
    def __init__(self, key, title, brand, keyword, risk_level, sentiment_expect,
                 batches, description=""):
        self.key = key
        self.title = title
        self.brand = brand
        self.keyword = keyword
        self.risk_level = risk_level
        # 期望情感分布，供自检断言
        self.sentiment_expect = sentiment_expect
        self.batches = batches
        self.description = description

    @property
    def total_weight(self):
        return sum(b.weight for b in self.batches)


# --------------------------- 场景 1：消费电子质量争议 ---------------------------

SCENARIO_CONSUMER_ELECTRONICS = Scenario(
    key="consumer_electronics",
    title="星耀科技 X-Phone 15 发热与续航质量争议",
    brand="星耀科技",
    keyword="星耀XPhone15",
    risk_level="S",
    sentiment_expect={"negative": 0.58, "neutral": 0.24, "positive": 0.18},
    description=(
        "新机发售后用户陆续反馈发热与续航问题，数码博主深度评测成为引爆点，"
        "随后论坛技术长帖与媒体转载推动扩散，官方发布声明后舆情逐步转向。"
    ),
    batches=[
        # T-72h ~ T-60h：零星用户反馈（首发节点）
        Batch(-4300, SOURCE_SOCIAL, ROLE_ORIGIN, ["derived"],
              {"negative": 0.75, "neutral": 0.25}, weight=1.4, spread_min=600),
        Batch(-4150, SOURCE_VIDEO, ROLE_ORIGIN, ["derived"],
              {"negative": 0.7, "neutral": 0.3}, weight=0.8, spread_min=300),
        # T-66h：KOL 深度评测 —— 引爆点
        Batch(-3960, SOURCE_SOCIAL, ROLE_IGNITION, ["kol_tech_zhang"],
              {"negative": 0.85, "neutral": 0.15}, weight=0.6, spread_min=20),
        Batch(-3900, SOURCE_VIDEO, ROLE_IGNITION, ["kol_digital_lab"],
              {"negative": 0.8, "neutral": 0.2}, weight=0.5, spread_min=25),
        # T-60h ~ T-48h：扩散
        Batch(-3600, SOURCE_SOCIAL, ROLE_SPREAD, ["derived"],
              {"negative": 0.72, "neutral": 0.18, "positive": 0.10}, weight=3.2, spread_min=700),
        Batch(-3300, SOURCE_VIDEO, ROLE_SPREAD, ["derived"],
              {"negative": 0.68, "neutral": 0.20, "positive": 0.12}, weight=2.0, spread_min=500),
        # T-48h：论坛技术分析长帖
        Batch(-2880, SOURCE_FORUM, ROLE_ANALYSIS, ["kol_chip_insider", "forum_mod_digital"],
              {"negative": 0.55, "neutral": 0.40, "positive": 0.05}, weight=1.6, spread_min=400),
        # T-42h：水军刷量（SimHash 演示样本）
        Batch(-2520, SOURCE_SOCIAL, ROLE_SPREAD, ["bot_pool"],
              {"negative": 0.95, "neutral": 0.05}, weight=0.7, spread_min=10, repeat=6),
        # T-36h：媒体跟进
        Batch(-2160, SOURCE_NEWS, ROLE_FOLLOWUP,
              ["media_kejichuanzhen", "media_xiaofei_wanbao"],
              {"negative": 0.45, "neutral": 0.50, "positive": 0.05}, weight=1.8, spread_min=300),
        # T-30h：官方回应 —— 情绪转向
        Batch(-1800, SOURCE_SOCIAL, ROLE_OFFICIAL, ["brand_xingyao", "brand_xingyao_cs"],
              {"neutral": 0.70, "positive": 0.30}, weight=0.6, spread_min=40),
        Batch(-1700, SOURCE_NEWS, ROLE_FOLLOWUP, ["media_kejichuanzhen"],
              {"neutral": 0.75, "positive": 0.25}, weight=0.7, spread_min=200),
        # T-24h ~ T-0：回落期
        Batch(-1400, SOURCE_SOCIAL, ROLE_SPREAD, ["derived"],
              {"negative": 0.45, "neutral": 0.35, "positive": 0.20}, weight=2.4, spread_min=800),
        Batch(-1200, SOURCE_FORUM, ROLE_ANALYSIS, ["derived"],
              {"negative": 0.42, "neutral": 0.45, "positive": 0.13}, weight=1.0, spread_min=600),
        Batch(-900, SOURCE_VIDEO, ROLE_SPREAD, ["derived"],
              {"negative": 0.40, "neutral": 0.38, "positive": 0.22}, weight=1.4, spread_min=500),
        # 背景噪声（与事件弱相关的同类讨论）
        Batch(-3600, SOURCE_SOCIAL, ROLE_AMBIENT, ["derived"],
              {"negative": 0.25, "neutral": 0.55, "positive": 0.20}, weight=1.2, spread_min=3000),
        Batch(-1800, SOURCE_FORUM, ROLE_AMBIENT, ["derived"],
              {"negative": 0.30, "neutral": 0.50, "positive": 0.20}, weight=0.8, spread_min=1500),
    ],
)

# --------------------------- 场景 2：连锁餐饮食品安全 ---------------------------

SCENARIO_CATERING_FOOD_SAFETY = Scenario(
    key="catering_food_safety",
    title="味鲜居连锁门店后厨卫生问题曝光",
    brand="味鲜居",
    keyword="味鲜居后厨",
    risk_level="A",
    # 剧情刻意压缩在单小时内陡增，用于触发「1 小时负向声量环比激增 > 50%」预警。
    # 负向期望值取 0.62 而非更高：官方致歉声明下的评论以观望与认可为主，
    # 会拉低全场景负向率，这是有意为之的「情绪转向」，不应被当作实现偏差。
    sentiment_expect={"negative": 0.62, "neutral": 0.24, "positive": 0.14},
    description=(
        "顾客在短视频平台曝光门店后厨卫生问题，同城用户迅速跟拍转发，"
        "声量在一小时内陡增触发预警；监管部门账号回应后，品牌发布致歉声明与整改承诺，舆情于 24 小时后转缓。"
    ),
    batches=[
        # T-26h：曝光视频（首发节点）
        Batch(-1560, SOURCE_VIDEO, ROLE_ORIGIN, ["user_food_hunter"],
              {"negative": 0.90, "neutral": 0.10}, weight=0.5, spread_min=8),
        # T-25.2h：10 分钟内同城跟拍，声量陡增
        Batch(-1510, SOURCE_VIDEO, ROLE_SPREAD, ["derived"],
              {"negative": 0.88, "neutral": 0.08, "positive": 0.04}, weight=1.6, spread_min=10),
        Batch(-1500, SOURCE_SOCIAL, ROLE_SPREAD, ["derived"],
              {"negative": 0.82, "neutral": 0.12, "positive": 0.06}, weight=2.6, spread_min=15),
        # T-25h：本地 KOL 转发（引爆）
        Batch(-1500, SOURCE_VIDEO, ROLE_IGNITION, ["kol_eat_local"],
              {"negative": 0.85, "neutral": 0.15}, weight=0.5, spread_min=12),
        # T-24.3h：监管部门回应 —— 高权重账号发声，触发第二类告警
        Batch(-1458, SOURCE_SOCIAL, ROLE_OFFICIAL, ["gov_food_safety"],
              {"neutral": 0.60, "negative": 0.30, "positive": 0.10}, weight=0.5, spread_min=25),
        # T-23h：消费者集中诉求
        Batch(-1380, SOURCE_SOCIAL, ROLE_SPREAD, ["derived"],
              {"negative": 0.78, "neutral": 0.16, "positive": 0.06}, weight=3.0, spread_min=180),
        Batch(-1350, SOURCE_FORUM, ROLE_ANALYSIS, ["derived"],
              {"negative": 0.65, "neutral": 0.30, "positive": 0.05}, weight=0.8, spread_min=200),
        # T-21h：水军刷量
        Batch(-1260, SOURCE_VIDEO, ROLE_SPREAD, ["bot_pool"],
              {"negative": 0.95, "neutral": 0.05}, weight=0.6, spread_min=8, repeat=6),
        # T-21h：品牌致歉声明 —— 转向
        Batch(-1260, SOURCE_SOCIAL, ROLE_OFFICIAL, ["brand_weixianju"],
              {"neutral": 0.65, "positive": 0.35}, weight=0.6, spread_min=30),
        # T-20h：媒体跟进
        Batch(-1200, SOURCE_NEWS, ROLE_FOLLOWUP, ["media_city_news", "media_health_weekly"],
              {"negative": 0.50, "neutral": 0.45, "positive": 0.05}, weight=1.6, spread_min=240),
        # T-12h ~ T-0：转缓
        Batch(-720, SOURCE_SOCIAL, ROLE_SPREAD, ["derived"],
              {"negative": 0.40, "neutral": 0.40, "positive": 0.20}, weight=2.0, spread_min=500),
        Batch(-360, SOURCE_VIDEO, ROLE_AMBIENT, ["derived"],
              {"negative": 0.25, "neutral": 0.50, "positive": 0.25}, weight=1.2, spread_min=300),
        # 背景噪声
        Batch(-1000, SOURCE_SOCIAL, ROLE_AMBIENT, ["derived"],
              {"negative": 0.20, "neutral": 0.55, "positive": 0.25}, weight=1.0, spread_min=2000),
    ],
)

# --------------------------- 场景 3：行业政策利好（对照） ---------------------------

SCENARIO_POLICY_BENEFIT = Scenario(
    key="policy_benefit",
    title="新能源汽车购置税减免政策延续",
    brand="新能源车购置税政策",
    keyword="新能源车购置税",
    risk_level="C",
    # 正向对照：用于验证情感分布不全是负面，且正向舆情不误报高危预警
    sentiment_expect={"positive": 0.52, "neutral": 0.38, "negative": 0.10},
    description=(
        "主管部门发布购置税减免延续公告，财经媒体密集解读，行业论坛围绕产业影响展开讨论，"
        "整体情绪以正向与中性为主，仅少量对执行细节的质疑。"
    ),
    batches=[
        # T-70h：政策公告（首发）
        Batch(-4200, SOURCE_NEWS, ROLE_ORIGIN, ["gov_industry_dept"],
              {"positive": 0.60, "neutral": 0.40}, weight=0.5, spread_min=20),
        # T-68h：财经媒体解读
        Batch(-4080, SOURCE_NEWS, ROLE_FOLLOWUP, ["media_finance_daily", "media_industry_observer"],
              {"positive": 0.55, "neutral": 0.42, "negative": 0.03}, weight=2.2, spread_min=400),
        Batch(-3900, SOURCE_NEWS, ROLE_ANALYSIS, ["media_industry_observer"],
              {"positive": 0.45, "neutral": 0.55}, weight=1.2, spread_min=300),
        # T-60h：社交平台讨论
        Batch(-3600, SOURCE_SOCIAL, ROLE_SPREAD, ["derived"],
              {"positive": 0.58, "neutral": 0.32, "negative": 0.10}, weight=2.4, spread_min=900),
        Batch(-3400, SOURCE_SOCIAL, ROLE_IGNITION, ["kol_auto_reviewer"],
              {"positive": 0.70, "neutral": 0.25, "negative": 0.05}, weight=0.6, spread_min=30),
        # T-48h：论坛产业影响讨论
        Batch(-2880, SOURCE_FORUM, ROLE_ANALYSIS, ["forum_auto_veteran", "derived"],
              {"positive": 0.42, "neutral": 0.46, "negative": 0.12}, weight=2.0, spread_min=600),
        # T-36h：地方配套细则（媒体）
        Batch(-2160, SOURCE_NEWS, ROLE_FOLLOWUP, ["media_finance_daily"],
              {"positive": 0.50, "neutral": 0.45, "negative": 0.05}, weight=1.4, spread_min=400),
        # T-24h ~ T-0：持续讨论
        Batch(-1200, SOURCE_SOCIAL, ROLE_SPREAD, ["derived"],
              {"positive": 0.48, "neutral": 0.42, "negative": 0.10}, weight=2.0, spread_min=800),
        Batch(-800, SOURCE_VIDEO, ROLE_SPREAD, ["derived"],
              {"positive": 0.55, "neutral": 0.35, "negative": 0.10}, weight=1.2, spread_min=500),
        # 背景噪声
        Batch(-2400, SOURCE_FORUM, ROLE_AMBIENT, ["derived"],
              {"positive": 0.35, "neutral": 0.50, "negative": 0.15}, weight=0.9, spread_min=1800),
    ],
)

SCENARIOS = [SCENARIO_CONSUMER_ELECTRONICS,
             SCENARIO_CATERING_FOOD_SAFETY,
             SCENARIO_POLICY_BENEFIT]

SCENARIO_BY_KEY = {s.key: s for s in SCENARIOS}

# ---------------------------------------------------------------------------
# 内容文案库
# 结构：SCENARIO_KEY -> ROLE -> [ {sentiment, emotion, title, texts[]} ]
# ---------------------------------------------------------------------------

CONTENT_LIBRARY = {
    "consumer_electronics": {
        ROLE_ORIGIN: [
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_DISAPPOINTMENT,
             "title": "新机到手三天的一些感受",
             "texts": [
                 "刚拿到X-Phone 15，玩半小时游戏后盖烫得不敢碰，这散热是认真的吗",
                 "X-Phone 15续航不太行啊，早上满电出门，下午三点就剩20%了，和宣传差太多",
                 "新机用了三天，发热比上一代明显，日常刷视频都温热，是不是这一代翻车了",
                 "买了X-Phone 15，充电时发热严重，客服说正常现象，我上一个手机也没这样啊",
                 "X-Phone 15这波有点失望，本来是冲着散热升级来的，结果比旧款还热",
             ]},
        ],
        ROLE_IGNITION: [
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_DISAPPOINTMENT,
             "title": "【实测】X-Phone 15 温度与续航深度测试",
             "texts": [
                 "【实测数据】X-Phone 15 满载温度 47.6℃，比上代高出 4.2℃；重度续航实测 5 小时 12 分，"
                 "官方标称 9 小时。这是我们今年测过差距最大的一次，说实话挺失望的。",
                 "连续测了三天 X-Phone 15，游戏二十分钟后帧率从 120 掉到 78，机身温度 46.8℃。"
                 "散热方案看起来没有兑现发布会的承诺，建议观望的同学再等等。",
             ]},
        ],
        ROLE_SPREAD: [
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_ANGER,
             "title": "",
             "texts": [
                 "看了评测，X-Phone 15这发热确实离谱，还好没下单",
                 "等了半年就等来这个？散热做不好就别吹啊",
                 "X-Phone 15发热问题不是个例了，评论区一堆人都在说",
                 "刚想换手机，看到这个评测直接劝退",
                 "宣传的冷静散热呢？发布会说得那么好听",
             ]},
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_DOUBT,
             "title": "",
             "texts": [
                 "这个测试条件是什么？环境温度多少，有没有说清楚",
                 "感觉数据和我的体感不太一样，我用的没这么夸张",
                 "评测机构的测试标准能不能公开一下",
                 "是不是工程机问题，量产机会不会好一点",
             ]},
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "",
             "texts": [
                 "先观望一下，等首销用户反馈多了再说",
                 "已经下单了，到手自己测测看，有结果回来汇报",
                 "看看官方会不会回应吧，现在说什么都早",
                 "我朋友买了说还好，可能批次问题？",
             ]},
            {"sentiment": SENTIMENT_POSITIVE, "emotion": EMOTION_NONE,
             "title": "",
             "texts": [
                 "我用的挺好的，日常用没有明显发热，可能是重载场景才有",
                 "散热这东西看使用习惯吧，我打游戏也没觉得烫到不能碰",
                 "同价位里性能还是能打的，发热可以接受",
             ]},
        ],
        ROLE_ANALYSIS: [
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_DOUBT,
             "title": "从散热结构看X-Phone 15这次的设计取舍",
             "texts": [
                 "拆解后可以看到，X-Phone 15 的VC均热板面积比上代缩水了约 18%，"
                 "同时SoC峰值功耗上浮，两者叠加导致长时间负载下温度堆积。"
                 "这不是软件能优化的问题，本质是硬件设计取舍。",
                 "从公开拆解图分析，X-Phone 15 中框导热路径存在瓶颈，"
                 "热量主要靠屏幕侧散出，握持时体感温度自然更高。"
                 "建议关注后续是否有硬件改版。",
             ]},
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "关于X-Phone 15发热的几点技术讨论",
             "texts": [
                 "客观说，今年的旗舰普遍发热压力都大，X-Phone 15 不是最差的，"
                 "但因为发布会把散热作为核心卖点，落差感被放大了。",
                 "讨论发热之前建议先统一测试口径：环境温度、亮度、网络条件都会影响结果，"
                 "目前各家的数据可比性并不强。",
             ]},
        ],
        ROLE_FOLLOWUP: [
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_NONE,
             "title": "X-Phone 15 发热争议持续，客服称属正常现象引用户不满",
             "texts": [
                 "近日，多位用户在社交平台反映星耀科技新机 X-Phone 15 存在发热与续航问题。"
                 "有用户称联系客服后被告知「属于正常现象」，该回应在网上引发进一步讨论。"
                 "截至发稿，星耀科技尚未发布正式说明。",
                 "星耀科技 X-Phone 15 上市一周，围绕机身发热的讨论持续发酵。"
                 "第三方测评机构公布的实测数据显示，该机型满载温度较上一代有所上升。"
                 "业内人士认为，散热卖点与实际体验的落差是本次争议的核心。",
             ]},
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "星耀科技回应X-Phone 15发热问题：将推送优化版本",
             "texts": [
                 "针对近期用户反馈，星耀科技今日发布说明称，已成立专项小组跟进，"
                 "后续将通过系统更新优化功耗调度策略，并为有需要的用户提供检测服务。"
                 "公司同时表示，产品各项指标均符合相关标准。",
                 "星耀科技就 X-Phone 15 发热问题作出回应，表示将优化高负载场景下的温控策略。"
                 "有分析认为，该事件对新机销量的实际影响仍有待观察。",
             ]},
        ],
        ROLE_OFFICIAL: [
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "关于X-Phone 15用户体验反馈的说明",
             "texts": [
                 "感谢大家对 X-Phone 15 的关注与反馈。我们已成立专项小组，"
                 "针对高负载场景下的温度与续航表现进行优化，相关系统更新将于近期推送。"
                 "如您的设备存在异常，可通过官方渠道申请免费检测。我们将持续改进，感谢理解与支持。",
                 "关于近期用户反馈的说明：我们注意到部分用户在使用高负载应用时对温度较为敏感。"
                 "工程师团队正在优化功耗调度策略，预计两周内推送更新版本。"
                 "同时为受影响用户开放一对一检测通道。",
             ]},
        ],
        ROLE_AMBIENT: [
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "",
             "texts": [
                 "最近想换手机，大家在纠结哪一款",
                 "手机用久了都会热吧，感觉现在讨论得有点夸张",
                 "有没有人对比过几款旗舰的续航表现",
                 "换机季到了，旧手机回收价格怎么样",
             ]},
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_DISAPPOINTMENT,
             "title": "",
             "texts": [
                 "现在的手机越来越贵，品控反而下滑了",
                 "旗舰机一年一换，实际升级感知越来越弱",
             ]},
        ],
    },

    "catering_food_safety": {
        ROLE_ORIGIN: [
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_ANGER,
             "title": "这就是味鲜居XX店的后厨，看完你还敢吃吗",
             "texts": [
                 "这就是味鲜居XX店的后厨，操作台油污堆积，食材直接放在地上，"
                 "我以后再也不会来了，太离谱了。",
                 "今天在味鲜居XX店看到的场景，我录下来了。"
                 "地面湿滑、垃圾桶没盖、员工没戴手套直接上手，真的受不了。",
             ]},
        ],
        ROLE_IGNITION: [
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_ANGER,
             "title": "味鲜居后厨问题，我实地去看了一趟",
             "texts": [
                 "看到那条视频后我特意去了趟现场，情况基本属实。"
                 "作为本地人，味鲜居是我常去的店，这次真的挺寒心的。"
                 "希望品牌方能给个说法，也希望监管部门查一查。",
             ]},
        ],
        ROLE_SPREAD: [
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_ANGER,
             "title": "",
             "texts": [
                 "味鲜居这么大的连锁也这样？以后再也不敢吃了",
                 "看完视频直接吃不下饭，这卫生条件太离谱了",
                 "必须严查！食品安全不能开玩笑",
                 "我上周才在味鲜居吃过，现在有点后怕",
                 "这种店就该停业整顿，别拿消费者健康当儿戏",
             ]},
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_PANIC,
             "title": "",
             "texts": [
                 "吃了会不会有问题啊，需要去检查一下吗",
                 "我们家孩子上周在那吃过，现在有点慌",
                 "会不会有其他门店也这样，不敢去了",
             ]},
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_DOUBT,
             "title": "",
             "texts": [
                 "视频是不是几年前的，希望核实一下时间",
                 "会不会是竞品故意抹黑，等官方调查结果",
                 "只有这一家店的问题还是普遍现象，需要说清楚",
             ]},
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_DISAPPOINTMENT,
             "title": "",
             "texts": [
                 "办卡充值的钱还能退吗，真的不想再去了",
                 "还是连锁品牌呢，管理这么松懈，失望",
                 "充了五百块，现在只能自认倒霉",
             ]},
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "",
             "texts": [
                 "等官方通报吧，先不下结论",
                 "看后续怎么处理，整改到位的话还是愿意给机会",
                 "希望监管部门尽快出结果",
             ]},
            {"sentiment": SENTIMENT_POSITIVE, "emotion": EMOTION_NONE,
             "title": "",
             "texts": [
                 "我常去的另一家分店挺干净的，可能是单店问题",
                 "先别一棒子打死，等调查结果出来再说",
             ]},
        ],
        ROLE_ANALYSIS: [
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_DOUBT,
             "title": "餐饮连锁的食品安全管理为什么容易出问题",
             "texts": [
                 "连锁扩张速度一旦超过管理半径，单店执行就容易走样。"
                 "总部有标准，但加盟店的日常巡检频次和惩罚力度往往跟不上，"
                 "这次事件本质是管理链条的问题，不是单个员工的问题。",
                 "从行业角度看，后厨可视化、明厨亮灶能解决一部分问题，"
                 "核心还是总部对门店的实际控制力。建议关注后续整改方案里有没有巡店机制。",
             ]},
        ],
        ROLE_OFFICIAL: [
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_NONE,
             "title": "关于网络反映我市某餐饮门店食品安全问题的通报",
             "texts": [
                 "针对网络反映的味鲜居相关门店食品安全问题，我局已第一时间派出执法人员现场核查。"
                 "目前已责令该门店停业整顿，后续将依法依规处理，并及时向社会公布结果。"
                 "欢迎广大市民监督举报。",
             ]},
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "关于味鲜居门店问题的致歉声明",
             "texts": [
                 "对于此次事件给消费者带来的担忧，我们深表歉意。"
                 "涉事门店已立即停业整改，公司将启动全国门店食品安全专项自查，"
                 "并升级后厨巡检频次与监控覆盖。已办理储值卡的用户可无条件退款。"
                 "我们将以最严格的标准整改，接受社会监督。",
                 "致歉声明：我们对涉事门店的问题负有管理责任。"
                 "即日起全面自查所有门店，延长后厨监控留存周期，"
                 "并设立消费者投诉专线。恳请公众给予我们改正的机会。",
             ]},
        ],
        ROLE_FOLLOWUP: [
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_NONE,
             "title": "味鲜居门店卫生问题引关注，监管部门已介入调查",
             "texts": [
                 "近日，一段反映味鲜居某门店后厨卫生状况的视频在网络传播，引发广泛关注。"
                 "当地市场监管部门表示已介入调查，涉事门店暂停营业。"
                 "味鲜居方面回应称将全面自查并接受监督。",
                 "味鲜居后厨卫生争议持续发酵。记者走访发现，涉事门店已停止营业。"
                 "多位消费者表示关注储值卡退款进展。专家指出，连锁餐饮的加盟管理半径是此类事件的共性症结。",
             ]},
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "味鲜居发布整改方案：全国门店延长监控留存并增设巡检",
             "texts": [
                 "味鲜居今日公布整改方案，承诺对全国门店开展食品安全专项自查，"
                 "后厨监控留存周期由 7 天延长至 30 天，并增设第三方不定期巡检。"
                 "监管部门表示将持续跟踪整改情况。",
             ]},
        ],
        ROLE_AMBIENT: [
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "",
             "texts": [
                 "最近有什么好吃的新店推荐吗",
                 "外卖和堂食哪个划算一点",
                 "求推荐干净又好吃的连锁店",
             ]},
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_NONE,
             "title": "",
             "texts": [
                 "现在外食越来越贵了，一顿饭人均六七十",
                 "餐饮卫生问题感觉隔段时间就曝一次",
             ]},
        ],
    },

    "policy_benefit": {
        ROLE_ORIGIN: [
            {"sentiment": SENTIMENT_POSITIVE, "emotion": EMOTION_NONE,
             "title": "关于延续新能源汽车车辆购置税减免政策的公告",
             "texts": [
                 "为支持新能源汽车产业发展，经研究决定，延续实施新能源汽车车辆购置税减免政策。"
                 "具体执行口径与时间安排详见附件。特此公告。",
             ]},
        ],
        ROLE_FOLLOWUP: [
            {"sentiment": SENTIMENT_POSITIVE, "emotion": EMOTION_NONE,
             "title": "购置税减免延续落地，新能源车市有望再获支撑",
             "texts": [
                 "政策延续为行业提供了稳定的预期。多家机构测算，"
                 "减免政策对终端销量的拉动效应在 5%~8% 区间，"
                 "对中低价位车型的边际影响更为明显。",
                 "业内人士认为，政策延续有助于平滑补贴退坡带来的需求波动，"
                 "产业链上游的电池与零部件企业有望同步受益。",
             ]},
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "购置税减免延续：政策细节与执行口径解读",
             "texts": [
                 "本次延续政策明确了适用车型范围与购置日期认定标准，"
                 "对换电车型与插电混动的执行口径也作出了说明。"
                 "消费者需注意发票开具时间与上牌时间的衔接要求。",
             ]},
        ],
        ROLE_IGNITION: [
            {"sentiment": SENTIMENT_POSITIVE, "emotion": EMOTION_NONE,
             "title": "购置税减免延续，现在是不是入手新能源车的好时机",
             "texts": [
                 "政策延续对准备买车的朋友是个实打实的好消息。"
                 "结合目前终端优惠，20 万以内的车型落地成本能省下不少。"
                 "我把几个主流车型的实际购车成本算了一遍，供大家参考。",
             ]},
        ],
        ROLE_SPREAD: [
            {"sentiment": SENTIMENT_POSITIVE, "emotion": EMOTION_NONE,
             "title": "",
             "texts": [
                 "这个政策好，本来还在犹豫，现在可以下手了",
                 "购置税减免延续，确实能省一笔",
                 "对刚需家庭来说是利好，早买早享受",
                 "准备换车，这波政策来得及时",
                 "新能源车这几年发展确实快，政策也跟得上",
             ]},
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "",
             "texts": [
                 "关键还是看执行细则，具体怎么认定",
                 "政策是好的，就看落地时间",
                 "先看看有没有配套的地方补贴",
                 "对已购车用户有没有影响，需要说明一下",
             ]},
            {"sentiment": SENTIMENT_NEGATIVE, "emotion": EMOTION_DOUBT,
             "title": "",
             "texts": [
                 "减免的钱会不会被经销商吃掉了，最终到手优惠未必多",
                 "政策延续是好事，但充电设施和保值率才是关键",
                 "希望别又变成厂家的营销噱头",
             ]},
        ],
        ROLE_ANALYSIS: [
            {"sentiment": SENTIMENT_POSITIVE, "emotion": EMOTION_NONE,
             "title": "从产业链角度看购置税政策延续的影响",
             "texts": [
                 "政策延续稳定了 2027 年前的终端需求预期，"
                 "对电池、电驱、热管理等环节的排产计划都有正向作用。"
                 "需要注意的是，行业竞争格局并未改变，价格战压力仍在。",
             ]},
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "政策延续之后，行业还需要解决哪些问题",
             "texts": [
                 "减免政策解决的是短期需求问题，中长期仍要看补能网络、"
                 "二手车残值体系和电池回收体系。建议关注后续配套政策出台节奏。",
                 "政策利好之外，行业自身的产品力提升才是根本。"
                 "从论坛反馈看，用户对续航真实性和冬季表现的关注度正在上升。",
             ]},
        ],
        ROLE_AMBIENT: [
            {"sentiment": SENTIMENT_NEUTRAL, "emotion": EMOTION_NONE,
             "title": "",
             "texts": [
                 "新能源和油车到底怎么选",
                 "冬天续航打折严重吗，北方的朋友说说",
                 "家充桩装起来麻烦吗",
             ]},
            {"sentiment": SENTIMENT_POSITIVE, "emotion": EMOTION_NONE,
             "title": "",
             "texts": [
                 "开了两年电车，回不去了",
                 "用车成本是真的低",
             ]},
        ],
    },
}

# ---------------------------------------------------------------------------
# 评论文案库
# 结构：SCENARIO_KEY -> SENTIMENT -> EMOTION -> [文本]
# 同一情绪下提供多种表达，保证聚类能分成有意义的簇而不是单一重复文本
# ---------------------------------------------------------------------------

COMMENT_LIBRARY = {
    "consumer_electronics": {
        SENTIMENT_NEGATIVE: {
            EMOTION_ANGER: [
                "退钱！这散热也好意思叫旗舰",
                "再也不会买星耀了，太让人生气",
                "宣传的冷静散热呢？这就是虚假宣传吧",
                "花了六千多买个暖手宝，服了",
                "这波必须维权，不能惯着",
                "离谱，我昨天刚下单，现在就这评价",
            ],
            EMOTION_DISAPPOINTMENT: [
                "说实话有点失望，等了大半年",
                "冲着散热升级来的，结果比上代还热",
                "本来挺期待的，现在有点后悔",
                "星耀这几年是越来越不行了",
                "唉，早知道再等等",
            ],
            EMOTION_DOUBT: [
                "这个测试的条件是什么，能不能公开一下",
                "感觉数据和我的体感不太一样",
                "是不是工程机的问题",
                "测评机构有没有恰饭，说清楚啊",
                "先看看有没有后续复测吧",
            ],
            EMOTION_PANIC: [
                "这么烫会不会有安全问题啊",
                "电池会不会受影响，有点担心",
                "会不会用着用着就鼓包了",
            ],
        },
        SENTIMENT_NEUTRAL: {
            EMOTION_NONE: [
                "先观望一下，等双十一再说",
                "等首销反馈多了再决定",
                "已经下单了，到手自己测测",
                "看看官方怎么回应吧",
                "同价位还有什么可以选的",
                "这个价位段的机器都差不多吧",
            ],
        },
        SENTIMENT_POSITIVE: {
            EMOTION_NONE: [
                "我用的挺好的，日常没什么感觉",
                "打游戏也不至于烫到不能碰",
                "同价位性能确实能打",
                "可能是我使用强度低，没遇到这问题",
                "星耀的屏幕和影像还是可以的",
            ],
        },
    },

    "catering_food_safety": {
        SENTIMENT_NEGATIVE: {
            EMOTION_ANGER: [
                "太离谱了，这种店就该停业整顿",
                "必须严查，食品安全不能开玩笑",
                "以后再也不会去了，恶心到我了",
                "拿消费者健康当儿戏，必须给个说法",
                "这管理水平也好意思做连锁",
                "看得我血压上来了",
            ],
            EMOTION_PANIC: [
                "我上周才在那吃过，现在有点后怕",
                "吃了会不会有问题，需要去检查吗",
                "我们家孩子在那吃过，有点慌",
                "其他门店会不会也这样",
            ],
            EMOTION_DOUBT: [
                "视频是什么时候拍的，希望核实一下",
                "会不会是故意抹黑，等调查结果",
                "只有这一家还是普遍现象，说清楚",
            ],
            EMOTION_DISAPPOINTMENT: [
                "办卡的五百块能退吗",
                "还是连锁品牌，管理这么松懈",
                "以后只能自己在家做饭了",
            ],
        },
        SENTIMENT_NEUTRAL: {
            EMOTION_NONE: [
                "等官方通报吧，先不下结论",
                "看后续整改情况再决定",
                "希望监管部门尽快出结果",
                "先观望，暂时不去就是了",
            ],
        },
        SENTIMENT_POSITIVE: {
            EMOTION_NONE: [
                "我常去的那家分店挺干净的",
                "别一棒子打死，等调查吧",
            ],
        },
    },

    "policy_benefit": {
        SENTIMENT_POSITIVE: {
            EMOTION_NONE: [
                "这政策不错，早该这样了",
                "本来在犹豫，这下可以下手了",
                "能省一笔是一笔，挺实在的",
                "对刚需家庭是利好",
                "准备换车，来得正是时候",
                "希望后续配套也能跟上",
            ],
        },
        SENTIMENT_NEUTRAL: {
            EMOTION_NONE: [
                "关键还是看执行细则",
                "先看看有没有地方补贴配套",
                "对已购车的用户有影响吗",
                "政策是好的，落地更重要",
                "等等看具体怎么认定",
            ],
        },
        SENTIMENT_NEGATIVE: {
            EMOTION_DOUBT: [
                "减免的钱会不会被经销商吃掉",
                "充电设施不解决都是空谈",
                "希望别变成厂家的营销噱头",
            ],
        },
    },
}

# ---------------------------------------------------------------------------
# 水军刷量文案（同一批次内高度相似，仅标点/表情差异，供 SimHash 演示）
# ---------------------------------------------------------------------------

BOT_TEMPLATES = {
    "consumer_electronics": [
        "星耀这波真的太让人失望了，果断取关",
        "星耀X-Phone 15就是个坑，大家千万别买",
        "这quality也太差了，谁买谁后悔",
    ],
    "catering_food_safety": [
        "味鲜居太脏了，大家都别去了",
        "这卫生条件真的恶心，抵制味鲜居",
        "以后再也不吃味鲜居了，太可怕",
    ],
}

# 标点与表情装饰，用于制造「近似但不完全相同」的刷量文本
BOT_DECORATIONS = [
    ("", ""), ("！", ""), ("！！", ""), ("～", "～"), ("", "😡"), ("！", "🤢"),
    ("", " 顶"), ("，大家扩散", ""), (" 转发扩散", ""), ("！！！", ""),
]

# ---------------------------------------------------------------------------
# 困难样本（评测集专用）
# 这些样本才是区分模型好坏的考场：反讽、混合情感、纯事实、表情主导、超短文本。
# 若评测集只由简单样本构成，85% 的准确率门槛形同虚设。
# ---------------------------------------------------------------------------

HARD_SAMPLES = [
    # 反讽（表面正向，实际负向）
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "星耀这散热真是绝了，冬天都不用买暖手宝了，谢谢星耀帮我们省钱",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_ANGER,
     "note": "反讽：用赞美句式表达不满"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "厉害了我的耀，发布会吹的散热，实测直接原地烤红薯",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_ANGER,
     "note": "反讽 + 网络梗"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_VIDEO,
     "text": "味鲜居这后厨真干净啊，干净得我都不敢下筷子了",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_ANGER,
     "note": "反讽：以肯定词表达否定"},
    {"scenario": "policy_benefit", "source_type": SOURCE_FORUM,
     "text": "政策真好，好到我都怀疑经销商会不会把优惠吃干净",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DOUBT,
     "note": "反讽 + 隐含质疑"},

    # 混合情感（正负并存，需要判定主导倾向）
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "屏幕和影像确实没得挑，就是这发热让我有点犹豫要不要退，纠结中",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DOUBT,
     "note": "混合情感，主导为负向（犹豫/退货倾向）"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_FORUM,
     "text": "性能释放没问题，日常也流畅，唯独长时间游戏温度压不住，算是瑕不掩瑜吧",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "混合情感，整体偏中性评价"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "味道我是真喜欢，但这次卫生问题确实让人没法接受，希望整改后还能吃",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DISAPPOINTMENT,
     "note": "混合情感，主导为负向（失望）"},
    {"scenario": "policy_benefit", "source_type": SOURCE_NEWS,
     "text": "政策利好毋庸置疑，但充电桩建设跟不上，长期效果还要观察",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "混合情感，整体中性"},

    # 纯事实陈述（无情感倾向）
    {"scenario": "consumer_electronics", "source_type": SOURCE_NEWS,
     "text": "星耀科技于今日发布说明，称将通过系统更新优化功耗调度策略",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "纯事实：无情感词"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_NEWS,
     "text": "X-Phone 15 于本月正式开售，起售价与上一代持平",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "纯事实：产品发布信息"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_NEWS,
     "text": "当地市场监管部门已派员现场核查，涉事门店暂停营业",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "纯事实：官方处置进展"},
    {"scenario": "policy_benefit", "source_type": SOURCE_NEWS,
     "text": "本次政策明确了适用车型范围与购置日期认定标准",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "纯事实：政策条文说明"},

    # 表情符号主导
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "🤢🤢🤢",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_ANGER,
     "note": "表情主导：需依赖表情语义"},
    {"scenario": "policy_benefit", "source_type": SOURCE_SOCIAL,
     "text": "🎉🎉 终于等到了",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "表情主导：正向"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "😅",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "表情主导：单一中性表情，信息量极低"},

    # 超短文本
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "退了",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DISAPPOINTMENT,
     "note": "超短：需结合语境判定"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_VIDEO,
     "text": "不敢去了",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_PANIC,
     "note": "超短：恐惧倾向"},
    {"scenario": "policy_benefit", "source_type": SOURCE_SOCIAL,
     "text": "可以",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "超短：中性答复"},

    # 中性疑问（易被误判为负向质疑）
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "请问这个测试的环境温度是多少度",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "疑问句但无质疑情绪，属中性"},
    {"scenario": "policy_benefit", "source_type": SOURCE_FORUM,
     "text": "有人知道具体什么时候开始执行吗",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "中性提问"},

    # 正向但含负面词（易被误判）
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "别再黑星耀了，我用了两周一次过热关机都没出现过，别被带节奏",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "含否定与负面词，实为正向辩护"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "这次整改方案挺有诚意的，希望别辜负大家",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "含吐槽对象但有正向期待"},

    # 引用他人观点（情感归属模糊）
    {"scenario": "consumer_electronics", "source_type": SOURCE_FORUM,
     "text": "转载一下老张的结论，具体数据大家自己判断，我不做评价",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "转述他人观点，标注者本身无倾向"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_NEWS,
     "text": "有网友称该门店此前曾被投诉，但该说法尚未得到证实",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "媒体报道措辞，未确认信息"},

    # ---- 反讽与阴阳怪气 ----
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "星耀的品控真是业界良心，一个月返修两次，服务还特别周到",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_ANGER,
     "note": "反讽：全用褒义词表达强烈不满"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "味鲜居这波公关太专业了，出事第一件事是删评论，学到了",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_ANGER,
     "note": "反讽 + 讽刺危机公关"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "买之前：散热旗舰。买之后：暖手宝旗舰。",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DISAPPOINTMENT,
     "note": "对仗式反差表达"},
    {"scenario": "policy_benefit", "source_type": SOURCE_SOCIAL,
     "text": "政策一出，4S店优惠立马缩水，真是及时雨啊",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DOUBT,
     "note": "反讽：表面称赞实则质疑政策红利被渠道吃掉"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "谢谢星耀，让我提前体验了这个冬天的温暖",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DISAPPOINTMENT,
     "note": "反讽：以感谢句式表达对发热的不满"},

    # ---- 混合情感：正负并存，需判定主导倾向 ----
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "外观手感是我用过最好的，但发热实在忍不了，已经挂闲鱼了",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DISAPPOINTMENT,
     "note": "混合情感，主导为负向（已挂二手）"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "客服态度挺好的，主动给我换了新机，但新机还是热",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DISAPPOINTMENT,
     "note": "混合情感：服务正向但产品负向，主导为负"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "整改后我去吃了，后厨开放参观，感觉挺用心的，就是想到之前的视频还是有点膈应",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "混合情感，整体偏中性"},
    {"scenario": "policy_benefit", "source_type": SOURCE_FORUM,
     "text": "对消费者是好事，对车企的利润是压力，谁受益还得看具体车型",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "多主体视角，整体中性"},

    # ---- 隐性负向：没有明显负面词 ----
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "建议先别买，等我朋友的维修结果出来再说",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DOUBT,
     "note": "隐性负向：劝阻购买，无负面情绪词"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "已经准备走12315了",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_ANGER,
     "note": "维权动作，无情绪形容词"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_FORUM,
     "text": "同价位隔壁厂都不这样，星耀这次确实是退步了",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DISAPPOINTMENT,
     "note": "对比式负向评价"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "发布会吹的均热板呢？缩水了还叫升级？",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DOUBT,
     "note": "反问式质疑"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_FORUM,
     "text": "冰箱温度计都不放，这也能叫后厨",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_ANGER,
     "note": "以专业细节表达不满"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "充了三张卡，现在只想退款",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DISAPPOINTMENT,
     "note": "诉求导向的负向"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "客服打了三次电话都在踢皮球，一次比一次客气",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_ANGER,
     "note": "隐性负向 + 反讽"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "这批次会不会都有问题，我们家老小都去吃过",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_PANIC,
     "note": "担忧主导型负向"},

    # ---- 超短文本（信息量极低）----
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "劝退",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_DISAPPOINTMENT,
     "note": "超短：劝阻态度"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "这质量我真的会谢",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_ANGER,
     "note": "网络梗：'会谢'表强烈不满"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "拉黑",
     "gold_sentiment": SENTIMENT_NEGATIVE, "gold_emotion": EMOTION_ANGER,
     "note": "超短：两字表达抵制"},
    {"scenario": "policy_benefit", "source_type": SOURCE_SOCIAL,
     "text": "mark",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "超短：仅标记，无情感"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "路过看看",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "超短：中性围观"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "挺住",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "超短：鼓励态度"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "支持",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "超短：正向支持"},

    # ---- 纯事实 / 数据陈述 ----
    {"scenario": "consumer_electronics", "source_type": SOURCE_NEWS,
     "text": "该机型官方标称续航 9 小时，第三方实测 5 小时 12 分",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "数据对比陈述，本身不含倾向"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_FORUM,
     "text": "不同批次的元器件供应商可能不同，这个不好一概而论",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "客观分析，中性"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_NEWS,
     "text": "涉事门店已停止营业，整改方案尚在制定中",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "纯事实：处置进展"},
    {"scenario": "policy_benefit", "source_type": SOURCE_NEWS,
     "text": "本次政策执行期为三年，自公告发布之日起施行",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "纯事实：政策时效"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_FORUM,
     "text": "说实话我不太懂散热这块，还是看实测数据说话吧",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "自述不懂，无倾向"},

    # ---- 中性疑问（易被误判为质疑）----
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "请问这个型号和上一代比主要升级了什么",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "中性提问"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "退款流程一般要走几天",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "中性询问流程，不含指责"},
    {"scenario": "policy_benefit", "source_type": SOURCE_SOCIAL,
     "text": "新能源车和混动的认定标准是一样的吗",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "中性政策咨询"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_FORUM,
     "text": "先码住，等后续",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "打卡式中性留言"},

    # ---- 转述与不确定信息 ----
    {"scenario": "consumer_electronics", "source_type": SOURCE_FORUM,
     "text": "据媒体报道，监管部门已介入，具体情况以官方通报为准",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "转述 + 免责，无个人倾向"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_FORUM,
     "text": "群里有人说已经收到检测通知了，不确定真假",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "未证实信息转述"},
    {"scenario": "policy_benefit", "source_type": SOURCE_FORUM,
     "text": "知乎上有个分析写得挺细的，链接我放下面了，大家自己看",
     "gold_sentiment": SENTIMENT_NEUTRAL, "gold_emotion": EMOTION_NONE,
     "note": "仅提供信息源，无倾向"},

    # ---- 正向但含负面词/否定结构（易被误判）----
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "别被带节奏，我自己测了两次都没出现过热的问题",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "含否定词与负面话题词，实为正向辩护"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "希望味鲜居能挺过去，确实挺喜欢他家的菜",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "正向期待，但提及负面事件"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "用了半个月没任何毛病，可能是我运气好",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "正向体验陈述"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_FORUM,
     "text": "同价位确实没有更好的选择，这是我换机时的结论",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "正向评价"},
    {"scenario": "consumer_electronics", "source_type": SOURCE_SOCIAL,
     "text": "等固件更新，相信能解决，毕竟硬件底子在那",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "正向期待"},
    {"scenario": "policy_benefit", "source_type": SOURCE_SOCIAL,
     "text": "这个政策对刚需家庭太友好了，已经订车了",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "正向并已行动"},
    {"scenario": "policy_benefit", "source_type": SOURCE_FORUM,
     "text": "国产车这几年进步是真的看得见，这波政策是锦上添花",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "正向行业评价"},
    {"scenario": "catering_food_safety", "source_type": SOURCE_SOCIAL,
     "text": "其他分店我常去，一直挺干净的，别一竿子打翻一船人",
     "gold_sentiment": SENTIMENT_POSITIVE, "gold_emotion": EMOTION_NONE,
     "note": "正向但含批评语境"},
]


def get_hard_samples():
    """返回困难样本，供评测集构造使用。"""
    return list(HARD_SAMPLES)
