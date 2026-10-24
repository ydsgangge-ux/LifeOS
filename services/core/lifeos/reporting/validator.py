"""输出校验器（第 9.3 节）：模型结论必须过八条规则，否则视为失败。

校验发生在「模型输出 → 落库」之间：只要有一条不满足，就把问题列表交回流水线，
带上错误信息重试一次；仍不满足则整份报告降级（第 9.2 节第 5 步）。

每条 finding 的形状见第 9.3 节表：claim、figures、evidence_refs、strength，
可选 alternatives / experiment。第 8 条（被纠正过的字段渲染时加标记）由
render 模块在渲染阶段落地，这里不重复检查。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..i18n import DEFAULT_LOCALE, t
from .render import PLACEHOLDER_RE, resolve_ref

STRENGTHS = ("strong", "medium", "weak", "insufficient")

# 第 5 条：通用禁用表述；儿童档另外加三条。
# 基线与中文词表保持不变（`BANNED_ALWAYS` / `BANNED_CHILD` 即 zh-CN 基线），
# 其余语言各一份（P6 W5b.6）——否则换成 en/ja/es 后中文禁令词匹配不到任何东西，
# P5 建立的合规结论会静默失效（测试全绿也发现不了）。
BANNED_ALWAYS = ("诊断", "确诊", "治愈", "你的性格是", "智商是")
BANNED_CHILD = ("评分", "排名", "比较他人")
BANNED_ALWAYS_BY_LOCALE: dict[str, tuple[str, ...]] = {
    "zh-CN": BANNED_ALWAYS,
    "en": (
        "diagnosis",
        "diagnose",
        "diagnosed",
        "cure",
        "your personality is",
        "your iq is",
    ),
    "ja": ("診断", "確定診断", "治癒", "あなたの性格は", "知能指数"),
    "es": (
        "diagnóstico",
        "diagnosticar",
        "curar",
        "curación",
        "tu personalidad es",
        "coeficiente intelectual",
    ),
}
BANNED_CHILD_BY_LOCALE: dict[str, tuple[str, ...]] = {
    "zh-CN": BANNED_CHILD,
    "en": ("score", "ranking", "compare with others", "compared to others"),
    "ja": ("評価点", "順位", "他人と比較"),
    "es": ("puntuación", "clasificación", "comparar con otros"),
}
# 第 6 条：causal_claim 为 false 时不得出现的因果措辞（同样按语言取，
# 否则非因果结论里的因果措辞在非中文报告里检查不出来）。
CAUSAL_WORDS = ("导致", "造成")
CAUSAL_WORDS_BY_LOCALE: dict[str, tuple[str, ...]] = {
    "zh-CN": CAUSAL_WORDS,
    "en": (
        "causes",
        "caused",
        "leads to",
        "led to",
        "results in",
        "resulted in",
        "due to",
        "because of",
    ),
    "ja": ("原因", "のせい", "引き起こ", "につながる"),
    "es": ("causa", "provoca", "ocasiona", "debido a"),
}


def banned_words(age_band: str, locale: str) -> tuple[str, ...]:
    """按语言取第 5 条禁用词表；未知 locale 回退中文基线。"""
    always = BANNED_ALWAYS_BY_LOCALE.get(locale, BANNED_ALWAYS)
    if age_band in ("child", "teen"):
        return always + BANNED_CHILD_BY_LOCALE.get(locale, BANNED_CHILD)
    return always


def causal_words(locale: str) -> tuple[str, ...]:
    """按语言取第 6 条因果措辞；未知 locale 回退中文基线。"""
    return CAUSAL_WORDS_BY_LOCALE.get(locale, CAUSAL_WORDS)


# 第 2 条：占位符之外不得出现的数字与单位符号
_NUMBER_RE = re.compile(r"[0-9０-９]")
_FORBIDDEN_TEXT = ("%", "％", "¥", "￥", "$", "元")
_PLAN_CODE_RE = re.compile(r"plan_[A-Za-z0-9]+")


@dataclass(frozen=True)
class Issue:
    """一条校验失败项。rule 为第 9.3 节的规则序号，便于参数化测试与定位。"""

    path: str
    rule: int
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "rule": self.rule, "message": self.message}


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def as_dicts(self) -> list[dict[str, Any]]:
        return [issue.as_dict() for issue in self.issues]


def iter_findings(node: Any, path: str = "") -> list[tuple[str, dict[str, Any]]]:
    """递归找出所有带 claim 的对象——这样任意 output_schema 形状都能覆盖。"""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        if isinstance(node.get("claim"), str):
            found.append((path or "output", node))
            return found
        for key, value in node.items():
            found.extend(iter_findings(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(iter_findings(item, f"{path}[{index}]"))
    return found


def iter_strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    """找出输出里所有字符串字段——占位符可能出现在 claim 之外（如 uncertainty）。"""
    found: list[tuple[str, str]] = []
    if isinstance(node, str):
        found.append((path or "output", node))
    elif isinstance(node, dict):
        for key, value in node.items():
            found.extend(iter_strings(value, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(iter_strings(item, f"{path}[{index}]"))
    return found


def _texts(finding: dict[str, Any]) -> list[str]:
    out = [str(finding.get("claim") or "")]
    alternatives = finding.get("alternatives")
    if isinstance(alternatives, list):
        out.extend(str(item) for item in alternatives)
    experiment = finding.get("experiment")
    if isinstance(experiment, dict):
        out.append(str(experiment.get("action") or ""))
        out.append(str(experiment.get("success_criteria") or ""))
    return out


def _plan_codes(payload: dict[str, Any]) -> set[str]:
    plans = payload.get("plans")
    if not isinstance(plans, list):
        return set()
    return {str(row["ref"]) for row in plans if isinstance(row, dict) and row.get("ref")}


def _check_finding(
    path: str,
    finding: dict[str, Any],
    payload: dict[str, Any],
    min_n_by_key: dict[str, int],
    age_band: str,
    locale: str,
    issues: list[Issue],
) -> None:
    claim = str(finding.get("claim") or "")

    # 规则 1：至少一条 evidence_ref，且都要能在事实包里解析到
    refs = finding.get("evidence_refs")
    if not isinstance(refs, list) or not [r for r in refs if isinstance(r, str)]:
        issues.append(Issue(path, 1, t("report.invalid.evidence_missing")))
    else:
        for ref in refs:
            if not isinstance(ref, str) or resolve_ref(payload, ref) is None:
                issues.append(
                    Issue(path, 1, t("report.invalid.evidence_unknown", ref=ref))
                )

    # 规则 2：占位符之外不得出现阿拉伯数字、百分号、金额符号
    stripped = PLACEHOLDER_RE.sub("", claim)
    if _NUMBER_RE.search(stripped) or any(token in stripped for token in _FORBIDDEN_TEXT):
        issues.append(Issue(path, 2, t("report.invalid.raw_number")))

    # 规则 3：strong 需要引用指标 n 达门槛；相关性类不得 strong
    strength = finding.get("strength")
    if strength not in STRENGTHS:
        issues.append(Issue(path, 3, t("report.invalid.strength_unknown")))
    elif strength == "strong":
        correlated = False
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, str):
                continue
            if ref.startswith("insights"):
                correlated = True
            resolved = resolve_ref(payload, ref)
            if resolved is None or resolved.metric_key is None:
                continue
            row = resolved.row or {}
            minimum = min_n_by_key.get(resolved.metric_key, 1)
            count = row.get("n")
            if not isinstance(count, int) or count < minimum:
                issues.append(
                    Issue(
                        path,
                        3,
                        t("report.invalid.strong_n", ref=ref, need=minimum, got=count),
                    )
                )
        if correlated:
            issues.append(Issue(path, 3, t("report.invalid.strong_correlation")))

    # 规则 4：替代解释可选，但写了就不能是空串
    alternatives = finding.get("alternatives")
    if alternatives is not None:
        valid = isinstance(alternatives, list) and (
            not alternatives or any(isinstance(a, str) and a.strip() for a in alternatives)
        )
        if not valid:
            issues.append(Issue(path, 4, t("report.invalid.alternatives_empty")))

    # 规则 5：禁用表述（按语言取词表，见 banned_words）
    banned = banned_words(age_band, locale)
    for text in _texts(finding):
        hit = next((word for word in banned if word in text), None)
        if hit is not None:
            issues.append(Issue(path, 5, t("report.invalid.banned", word=hit)))

    # 规则 6：非因果结论不得使用因果措辞（同样按语言取词表）
    if not bool(finding.get("causal_claim", False)):
        causal = causal_words(locale)
        for text in (claim, *(alternatives if isinstance(alternatives, list) else [])):
            hit = next((word for word in causal if word in str(text)), None)
            if hit is not None:
                issues.append(Issue(path, 6, t("report.invalid.causal", word=hit)))

    # 规则 7：只能用事实包里出现过的计划代号
    known = _plan_codes(payload)
    for code in _PLAN_CODE_RE.findall(claim):
        if code not in known:
            issues.append(Issue(path, 7, t("report.invalid.unknown_code", code=code)))


def validate_output(
    output: Any,
    *,
    payload: dict[str, Any],
    min_n_by_key: dict[str, int] | None = None,
    age_band: str = "adult",
    locale: str = DEFAULT_LOCALE,
    require_findings: bool = True,
) -> ValidationResult:
    """校验整份模型输出；默认没有 findings 也算失败。

    分领域 agent 的产出允许只有一段小结、不列 findings，因此把「必须有 findings」
    做成开关；其余规则（依据可解析、占位符、禁用词、因果措辞）不变。
    """
    min_n = min_n_by_key or {}
    issues: list[Issue] = []
    findings = iter_findings(output)
    if not findings and require_findings:
        issues.append(Issue("output", 1, t("report.invalid.no_findings")))
        return ValidationResult(issues)
    for path, finding in findings:
        _check_finding(path, finding, payload, min_n, age_band, locale, issues)
    # 规则 2 附加：占位符本身必须解析得到（任意文字字段）。字段键写错不会被
    # 上面的数字检查发现，但渲染时会把 [[fact:...]] 原样显示给用户。
    for path, text in iter_strings(output):
        for ref in PLACEHOLDER_RE.findall(text):
            key = ref.strip()
            if resolve_ref(payload, key) is None:
                issues.append(Issue(path, 2, t("report.invalid.placeholder_unknown", ref=key)))
    return ValidationResult(issues)
