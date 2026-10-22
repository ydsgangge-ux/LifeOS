"""标识扫描（第 7.4 节第 4 步、第 7.5 节）：把可识别的个人标识挡在出站之前。

两道防线：正则扫一遍，再把文本归一化（全角→半角、剔除空格与千分位）后重扫，
覆盖「全角/半角、插入空格、千分位与小数写法」这些变体（第 7.5 节第 3 条）。

扫描结果只带分类计数，不带命中原文——第 7.4 节第 6 条要求审计日志不存内容。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

_FULLWIDTH_OFFSET = 0xFEE0
_IDEOGRAPHIC_SPACE = 0x3000
# 分隔与千分位写法：去掉后再匹配数字类标识，12,800 与 12 800 都能命中
_SEPARATORS = re.compile(r"[\s,，、_\-．.·]+")

# 数字类标识在「紧凑文本」上匹配
_COMPACT_PATTERNS: dict[str, re.Pattern[str]] = {
    "phone": re.compile(r"(?:\+?86)?1[3-9]\d{9}"),
    "id_card": re.compile(
        r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx]"
    ),
    "bank_card": re.compile(r"\d{12,19}"),
}

# 文本类标识在「保留分隔的归一化文本」上匹配
_PLAIN_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "url": re.compile(r"https?://\S+"),
}


def normalize(text: str) -> str:
    """全角→半角 + NFKC；保留空格与标点，供邮箱、网址这类标识匹配。"""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - _FULLWIDTH_OFFSET))
        elif code == _IDEOGRAPHIC_SPACE:
            out.append(" ")
        else:
            out.append(ch)
    return unicodedata.normalize("NFKC", "".join(out))


def compact(text: str) -> str:
    """剔除空格、逗号、千分位与小数写法，暴露被拆开的数字串。"""
    return _SEPARATORS.sub("", text)


@dataclass(frozen=True)
class ScanReport:
    """命中分类计数；不含命中原文。"""

    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def clean(self) -> bool:
        return self.total == 0

    def merge(self, other: ScanReport) -> ScanReport:
        merged = dict(self.counts)
        for kind, count in other.counts.items():
            merged[kind] = merged.get(kind, 0) + count
        return ScanReport(merged)


def scan_text(text: str, *, names: Iterable[str] = ()) -> ScanReport:
    """扫描一段文本；names 是本地人物表中的姓名与别名（第 7.4 节第 4 步）。"""
    plain = normalize(text)
    squeezed = compact(plain)
    counts: dict[str, int] = {}

    for kind, pattern in _PLAIN_PATTERNS.items():
        hits = len(pattern.findall(plain))
        if hits:
            counts[kind] = hits

    # 手机号与证件号先扫，并从紧凑文本里抹掉，避免同一串数字被重复算成银行卡号
    remainder = squeezed
    for kind in ("phone", "id_card"):
        pattern = _COMPACT_PATTERNS[kind]
        hits = len(pattern.findall(squeezed))
        if hits:
            counts[kind] = hits
        remainder = pattern.sub("", remainder)

    bank_hits = len(_COMPACT_PATTERNS["bank_card"].findall(remainder))
    if bank_hits:
        counts["bank_card"] = bank_hits

    name_hits = 0
    for term in names:
        needle = compact(normalize(term))
        if needle and needle in squeezed:
            name_hits += 1
    if name_hits:
        counts["person_name"] = name_hits

    return ScanReport(counts)
