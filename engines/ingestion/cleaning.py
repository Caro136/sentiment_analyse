"""文本清洗。

**原则是保守**：只去掉明确与分析无关的噪声，保留一切可能是信息的东西。

具体地，保留话题标签（`#星耀XPhone15#`）、@提及、表情符（`[比心]`、emoji）——
它们分别承载主题归属、实体信息与情感倾向，恰恰是分析需要的信号。
被去掉的只有 HTML 标签、URL、零宽字符与多余空白。

反面教训：把清洗做成"只留中文字符"看似干净，实际上会丢掉表情符号承载的情绪强度
（`[捂脸]` 与 `[发怒]` 是完全不同的态度），也会破坏话题标签带来的聚类线索。
"""

import re

# HTML 标签：采集器偶尔会把富文本原样带入
_HTML_TAG = re.compile(r"<[^>]{1,200}>")
# 链接：正文里的 URL 对语义分析没有贡献，且会干扰 SimHash 指纹
_URL = re.compile(r"(?:https?://|www\.)[^\s，。；）】\"'<>]+", re.IGNORECASE)
# 零宽字符与 BOM：肉眼不可见，但会让"看起来相同"的文本指纹不同，
# 是水军绕过文本去重的常见手段
_ZERO_WIDTH = re.compile(r"[​-‏‪-‮﻿]")
# 连续空格与制表符（含全角空格）
_INLINE_SPACE = re.compile(r"[ \t　 ]+")
# 三个以上连续换行压成两个
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """清洗一段文本。空输入返回空串，不做任何假设。"""
    if not text:
        return ""

    result = _ZERO_WIDTH.sub("", text)
    result = _HTML_TAG.sub(" ", result)
    result = _URL.sub(" ", result)
    result = _INLINE_SPACE.sub(" ", result)
    result = _MULTI_NEWLINE.sub("\n\n", result)
    return result.strip()


def normalize_for_hash(text: str) -> str:
    """归一化文本用于指纹计算。

    去掉标点与空白后再算指纹——水军刷量最常见的变体就是改标点、加空格
    （「太离谱了」「太离谱了！」「太 离谱 了」实际是同一句话），
    不归一化的话这些都会被当成不同内容，去重形同虚设。
    """
    if not text:
        return ""
    result = _ZERO_WIDTH.sub("", text)
    # 只保留中日韩文字、字母与数字
    result = re.sub(r"[^一-鿿a-zA-Z0-9]", "", result)
    return result.lower()


def has_meaningful_text(text: str, min_length: int = 2) -> bool:
    """判断清洗后是否还有实质内容。

    纯表情、纯标点的内容对文本分析无意义（但可能仍需要在界面上展示），
    接入层据此决定是否送去做文本分析，避免白花模型调用。
    """
    return len(normalize_for_hash(text)) >= min_length


def truncate(text: str, limit: int) -> str:
    """按字符数截断，用于列表展示与 Prompt 证据块。"""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"
