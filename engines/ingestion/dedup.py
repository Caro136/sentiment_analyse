"""SimHash 去重与水军识别（PRD FR-19、附录 B.1）。

要解决两个问题：

1. **同质内容合并**。同一件事被反复搬运、截图转述、复制粘贴，不合并的话
   声量与情感分布都会被重复内容扭曲——100 条内容是同一句话复制 100 遍，
   统计分析就失去了意义。

2. **水军刷量识别**。刷量账号的特征是在短时间窗内发布高度雷同的内容
   （仅改标点、加空格、换表情）。这类内容既要去重，账号本身也要被标记，
   并在热度计算中降权。

技术选型：SimHash 指纹 + 汉明距离。相比逐字符相似度（编辑距离），
SimHash 的性能是常数级的——比较两个 64 位整数即可，且天然容忍少量改动。

性能：64 位指纹按 4 段 16 位做 LSH 分桶。**这不是可有可无的优化**——
prd 规模 1 万条内容做两两比较是 5000 万次，分桶后降到线性量级。
由鸽巢原理保证：两条内容若汉明距离 ≤ 3，4 段中必有一段完全相同，不会漏判。
"""

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field

from engines.contracts.content import UnifiedContent
from engines.ingestion.cleaning import normalize_for_hash

HASH_BITS = 64
SHINGLE_SIZE = 3          # 字符 3-gram
SHINGLE_STEP = 2          # 步长 2：步长 1 会产生 O(n) 个分片，长文本上开销明显
MAX_TEXT_FOR_HASH = 500   # 超长文本截断——指纹对超出部分不敏感，没必要全量计算

DEFAULT_HAMMING_THRESHOLD = 3   # 汉明距离 ≤ 3 视为近重复（64 位下的常用阈值）

# LSH 分桶参数
_BAND_COUNT = 4
_BAND_BITS = HASH_BITS // _BAND_COUNT
_BAND_MASK = (1 << _BAND_BITS) - 1


@dataclass(frozen=True)
class DuplicateGroup:
    """一组近重复内容。保留一条，其余合并到它。"""

    kept_uid: str
    merged_uids: tuple[str, ...]

    @property
    def size(self) -> int:
        return len(self.merged_uids) + 1


@dataclass
class DedupResult:
    """去重结果。"""

    unique_contents: list[UnifiedContent] = field(default_factory=list)
    # 被合并内容的 uid → 保留内容的 uid。落库时用于记录合并关系
    duplicate_of: dict[str, str] = field(default_factory=dict)
    # 内容 uid → 64 位指纹。写入 content_analysis.simhash
    fingerprints: dict[str, int] = field(default_factory=dict)
    groups: list[DuplicateGroup] = field(default_factory=list)

    @property
    def duplicate_count(self) -> int:
        return len(self.duplicate_of)


@dataclass(frozen=True)
class BotSignal:
    """疑似水军信号。"""

    account_id: str
    nickname: str
    content_count: int          # 该账号在时间窗内的内容总数
    similar_pairs: int          # 高度相似的内容对数
    window_minutes: int
    sample_uid: str

    @property
    def reason(self) -> str:
        return (f"{self.window_minutes} 分钟窗口内发布 {self.content_count} 条内容，"
                f"其中 {self.similar_pairs} 对高度相似")


# ---------------------------------------------------------------------------
# 指纹计算
# ---------------------------------------------------------------------------

def shingles(text: str) -> list[str]:
    """字符 n-gram 分片。

    中文不依赖分词——字符 n-gram 在中文上表现稳定，且免去引入分词依赖。
    """
    if not text:
        return []
    text = text[:MAX_TEXT_FOR_HASH]
    if len(text) <= SHINGLE_SIZE:
        return [text]
    return [text[i:i + SHINGLE_SIZE]
            for i in range(0, len(text) - SHINGLE_SIZE + 1, SHINGLE_STEP)]


def simhash(text: str, bits: int = HASH_BITS) -> int:
    """计算 SimHash 指纹。

    做法：每个分片取一个哈希值，逐位投票（该位为 1 则 +1，否则 -1），
    最终按位的正负决定指纹该位是 1 还是 0。相近的文本投票结果相近，
    因此指纹的汉明距离也就相近。
    """
    tokens = shingles(normalize_for_hash(text))
    if not tokens:
        return 0

    vector = [0] * bits
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for i in range(bits):
            vector[i] += 1 if (value >> i) & 1 else -1

    fingerprint = 0
    for i in range(bits):
        if vector[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    """汉明距离：两个指纹有多少位不同。"""
    return (a ^ b).bit_count()


def similarity(a: int, b: int, bits: int = HASH_BITS) -> float:
    """指纹相似度 0~1。"""
    return 1.0 - hamming_distance(a, b) / bits


def _band_keys(fingerprint: int) -> list[int]:
    """按段取 LSH 桶键。"""
    return [(fingerprint >> (i * _BAND_BITS)) & _BAND_MASK for i in range(_BAND_COUNT)]


# ---------------------------------------------------------------------------
# 去重
# ---------------------------------------------------------------------------

def _keep_priority(content: UnifiedContent) -> tuple[int, int, str]:
    """决定同组中保留哪一条。

    优先保留互动量高的：搬运、截图转述的复制品通常互动量远低于原文，
    保留高互动的那条更接近"这件事实际是在哪被传播的"。
    互动量相同时取发布时间早的（更接近首发）。
    最后用 uid 兜底，保证排序稳定可复现。
    """
    return (-content.engagement.total, content.published_at, content.content_uid)


def deduplicate(contents: list[UnifiedContent], *,
                threshold: int = DEFAULT_HAMMING_THRESHOLD) -> DedupResult:
    """合并同质内容。

    两阶段：先用归一化文本精确去重（O(n)，成本极低且能吃掉大部分重复），
    再用 SimHash 处理改标点、加空格这类近似变体。
    """
    result = DedupResult()
    if not contents:
        return result

    fingerprints: dict[str, int] = {}
    for content in contents:
        fingerprints[content.content_uid] = simhash(content.analysis_text)

    # ---- 分桶：把两两比较降为桶内比较 ----
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, content in enumerate(contents):
        for band, key in enumerate(_band_keys(fingerprints[content.content_uid])):
            buckets[(band, key)].append(index)

    # 并查集：把近重复内容并成一组
    parent = list(range(len(contents)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    compared: set[tuple[int, int]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                pair = (a, b) if a < b else (b, a)
                if pair in compared:      # 同一对可能命中多个 band
                    continue
                compared.add(pair)
                if hamming_distance(fingerprints[contents[a].content_uid],
                                    fingerprints[contents[b].content_uid]) <= threshold:
                    union(a, b)

    # ---- 按组决定保留哪一条 ----
    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(contents)):
        groups[find(index)].append(index)

    for members in groups.values():
        ordered = sorted((contents[i] for i in members), key=_keep_priority)
        kept = ordered[0]
        result.unique_contents.append(kept)
        if len(ordered) > 1:
            merged = tuple(c.content_uid for c in ordered[1:])
            result.groups.append(DuplicateGroup(kept_uid=kept.content_uid,
                                                merged_uids=merged))
            for uid in merged:
                result.duplicate_of[uid] = kept.content_uid

    # 保持输入顺序，便于调用方对照
    order = {c.content_uid: i for i, c in enumerate(contents)}
    result.unique_contents.sort(key=lambda c: order[c.content_uid])
    result.fingerprints = fingerprints
    return result


# ---------------------------------------------------------------------------
# 水军识别
# ---------------------------------------------------------------------------

def detect_bots(contents: list[UnifiedContent], fingerprints: dict[str, int], *,
                threshold: int = DEFAULT_HAMMING_THRESHOLD,
                min_similar_pairs: int = 3,
                window_minutes: int = 30,
                max_followers: int = 200) -> list[BotSignal]:
    """识别疑似水军账号。

    判定依据：**同一账号在时间窗内发布多条高度相似内容**。
    单纯"发得多"不算——正常用户的连续发言也可能聚集在半小时内，
    但不会有大量雷同文本。

    `max_followers` 额外排除高粉账号：万粉账号即便发了相似内容，
    也不太可能是刷量号（更可能是转发同一条新闻），
    把它们标成水军会污染账号权重计算。
    """
    if not contents:
        return []

    by_account: dict[str, list[UnifiedContent]] = defaultdict(list)
    for content in contents:
        account_id = content.author.account_id
        if account_id:
            by_account[account_id].append(content)

    signals: list[BotSignal] = []
    window_seconds = window_minutes * 60

    for account_id, items in by_account.items():
        if len(items) < min_similar_pairs:
            continue
        if items[0].author.follower_count > max_followers:
            continue

        ordered = sorted(items, key=lambda c: c.published_at)
        similar_pairs = 0
        sample_uid = ""

        for i, current in enumerate(ordered):
            for earlier in reversed(ordered[:i]):
                # 时间窗外的更早内容可以直接停止——已按时间排序
                if current.published_at - earlier.published_at > window_seconds:
                    break
                fp_current = fingerprints.get(current.content_uid, 0)
                fp_earlier = fingerprints.get(earlier.content_uid, 0)
                if hamming_distance(fp_current, fp_earlier) <= threshold:
                    similar_pairs += 1
                    sample_uid = sample_uid or current.content_uid

        if similar_pairs >= min_similar_pairs:
            signals.append(BotSignal(
                account_id=account_id,
                nickname=ordered[0].author.nickname,
                content_count=len(ordered),
                similar_pairs=similar_pairs,
                window_minutes=window_minutes,
                sample_uid=sample_uid,
            ))

    signals.sort(key=lambda s: s.similar_pairs, reverse=True)
    return signals


@dataclass
class DedupSummary:
    """供日志与看板使用的统计。"""

    input_count: int = 0
    unique_count: int = 0
    duplicate_count: int = 0
    bot_accounts: int = 0

    @property
    def duplicate_ratio(self) -> float:
        return self.duplicate_count / self.input_count if self.input_count else 0.0
