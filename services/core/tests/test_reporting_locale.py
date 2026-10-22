"""报告语言与四语言禁令词（P6 W5b、验收 A5/A6）。

两块：

1. **本地报告的产出语言**：`build_degraded()` / `build_child_report()` 按 locale 出文字。
   `en`/`es` 不含汉字与假名；`ja` 必须含假名；`zh-CN` 含汉字。
   （`es`/`ja` 词表尚未铺开，见 p6-ja-es；词表缺失时该用例 skip，不是断言通过。）
2. **校验器的禁令词按语言取**：`BANNED_ALWAYS` / `BANNED_CHILD` / 因果措辞都要有
   四种语言各一份；用**该语言的违规样本**断言能检出，用**其他语言**断言不误伤——
   否则换语言后禁令形同虚设（最隐蔽的一条，见计划 5b.6/5b.7）。

注意：`ja` 用汉字。**不能**对所有语言一律断言「输出不含 CJK 字符」——那对日语
要么必然失败、要么是永远通过的假检查。
"""

from __future__ import annotations

import pytest

from lifeos.i18n import available_locales
from lifeos.reporting import build_child_report, validate_output
from lifeos.reporting.degrade import build_degraded
from lifeos.reporting.validator import (
    BANNED_ALWAYS_BY_LOCALE,
    BANNED_CHILD_BY_LOCALE,
    CAUSAL_WORDS_BY_LOCALE,
)

LOCALES = ("zh-CN", "en", "ja", "es")

# 一份最小事实包：一个达门槛指标、一个计划、一个缺口
_FACTS = {
    "metrics": [
        {
            "key": "plan.completion_rate",
            "description": "i18n:metric.plan.completion_rate",
            "unit": "%",
            "value": 0.8,
            "prev_value": 0.5,
            "delta": 0.3,
            "n": 3,
            "status": "ok",
        }
    ],
    "plans": [{"ref": "plan_A", "diagnosis": {"verdict": "retire", "reasons": []}}],
    "data_gaps": [{"key": "facts.gap.domain_empty", "params": {"domain": "dream"}}],
}

_PAYLOAD = {
    "metrics": [
        {
            "key": "health.sleep.mean_hours",
            "value": 7.5,
            "prev_value": 7.0,
            "delta": 0.5,
            "n": 6,
            "unit": "小时",
            "value_kind": "number",
        }
    ]
}
_MIN_N = {"health.sleep.mean_hours": 4}


def _has_han_or_kana(text: str) -> bool:
    return any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in text)


def _has_kana(text: str) -> bool:
    return any("\u3040" <= ch <= "\u30ff" for ch in text)


def _assert_output_language(text: str, locale: str) -> None:
    if locale in ("en", "es"):
        assert not _has_han_or_kana(text), f"{locale} 报告里混进了汉字/假名：{text}"
    elif locale == "ja":
        assert _has_kana(text), f"ja 报告里没有假名，可能回退成中文了：{text}"
    else:  # zh-CN
        assert _has_han_or_kana(text), "zh-CN 报告里没有汉字"


# ---------------------------------------------------------------------------
# 本地报告的产出语言
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("locale", LOCALES)
def test_degraded_report_uses_locale(locale: str) -> None:
    if locale not in available_locales():
        pytest.skip(f"{locale} 词表尚未铺开（见 p6-ja-es），补上后本用例自动生效")
    summary, _output = build_degraded(_FACTS, locale=locale)
    _assert_output_language(summary, locale)


@pytest.mark.parametrize("locale", LOCALES)
def test_child_report_uses_locale(locale: str) -> None:
    if locale not in available_locales():
        pytest.skip(f"{locale} 词表尚未铺开（见 p6-ja-es），补上后本用例自动生效")
    summary, _output = build_child_report(_FACTS, locale=locale)
    _assert_output_language(summary, locale)


# ---------------------------------------------------------------------------
# 校验器：禁令词按语言取
# ---------------------------------------------------------------------------
def _finding(**over):
    base = {
        "claim": "睡眠与精力同时变化 [[fact:health.sleep.mean_hours]]",
        "figures": [{"ref": "health.sleep.mean_hours", "format": "decimal1"}],
        "evidence_refs": ["metrics.health.sleep.mean_hours"],
        "strength": "medium",
        "causal_claim": False,
        "alternatives": ["样本太少"],
    }
    base.update(over)
    return base


def _rules(finding, *, age_band: str = "adult", locale: str = "zh-CN"):
    result = validate_output(
        {"highlights": [finding]},
        payload=_PAYLOAD,
        min_n_by_key=_MIN_N,
        age_band=age_band,
        locale=locale,
    )
    return {issue.rule for issue in result.issues}


@pytest.mark.parametrize("locale", LOCALES)
def test_banned_always_detected_per_locale(locale: str) -> None:
    word = BANNED_ALWAYS_BY_LOCALE[locale][0]
    assert 5 in _rules(_finding(claim=f"这句与{word}相关"), locale=locale)


@pytest.mark.parametrize("locale", LOCALES)
def test_banned_word_not_matched_in_other_locale(locale: str) -> None:
    """该语言的禁用词放到另一种语言下不应命中——证明禁令确实按语言取。"""
    other = "zh-CN" if locale != "zh-CN" else "en"
    word = BANNED_ALWAYS_BY_LOCALE[locale][0]
    assert 5 not in _rules(_finding(claim=f"x {word} y"), locale=other)


@pytest.mark.parametrize("locale", LOCALES)
def test_child_ban_detected_per_locale(locale: str) -> None:
    word = BANNED_CHILD_BY_LOCALE[locale][0]
    assert 5 in _rules(_finding(claim=f"x {word} y"), age_band="child", locale=locale)


@pytest.mark.parametrize("locale", LOCALES)
def test_causal_words_detected_per_locale(locale: str) -> None:
    word = CAUSAL_WORDS_BY_LOCALE[locale][0]
    assert 6 in _rules(_finding(claim=f"x {word} y"), locale=locale)


def test_unknown_locale_uses_chinese_baseline() -> None:
    assert 5 in _rules(_finding(claim="这句与诊断相关"), locale="xx-XX")
