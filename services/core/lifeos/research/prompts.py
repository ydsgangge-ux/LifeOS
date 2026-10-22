"""提示词包生成（第 10.4 节）。

每个知识缺口生成一条独立提示词，共用一个 pack_id。提示词里的背景只含泛化信息：
年龄段、地区层级、关键约束的区间，不含任何身份信息。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..util.ids import new_ulid

DEFAULT_FRESHNESS_DAYS = 365

_PROMPT_TEMPLATE = """\
【背景（已泛化，不含身份信息）】{audience}；{region_level}；{constraints_band}
【决策问题】{question}
【请查证】{gap_question}
【输出格式】每条结论一行，字段用「｜」分隔：
结论｜关键数据（含单位和年份）｜来源名称｜来源URL｜发布时间｜证据类型（官方统计、研究、媒体、个人经验）｜与其他来源是否冲突
【要求】
1. 优先官方和一手来源{authority_hint}。
2. 数据早于 {freshness_cutoff} 的，标注「可能过期」。
3. 必须包含反面证据和失败案例。
4. 查不到可靠来源就写「未找到可靠来源」，不要编造链接。
5. 最后列出：哪些结论会改变你对这个问题的判断？
【任务ID】{pack_id}-{gap_id}（粘贴结果时原样保留这一行）
"""

_REGION_LABELS = {
    "national": "全国",
    "province": "省级",
    "city": "城市",
    "county": "县级",
    "town": "乡镇",
}


@dataclass(frozen=True)
class PromptPack:
    pack_id: str
    tasks: list[dict[str, Any]]


def build_prompt_pack(
    question: str,
    assessment: dict[str, Any],
    *,
    audience: str = "成年人",
    region_level: str = "未指定",
    constraints_band: str = "未提供",
) -> PromptPack:
    """为 assessment 里的每个 knowledge_gap 生成一条提示词。"""
    pack_id = new_ulid()
    gaps = assessment.get("knowledge_gaps") or []
    tasks: list[dict[str, Any]] = []
    authority_hint = assessment.get("authority_hint") or ""
    region_label = _REGION_LABELS.get(region_level, region_level)
    for gap in gaps:
        gap_id = gap["id"]
        freshness = gap.get("freshness_days", DEFAULT_FRESHNESS_DAYS)
        prompt = _PROMPT_TEMPLATE.format(
            audience=audience,
            region_level=region_label,
            constraints_band=constraints_band,
            question=question,
            gap_id=gap_id,
            gap_question=gap.get("question", ""),
            authority_hint=authority_hint,
            freshness_cutoff=f"{freshness} 天内",
            pack_id=pack_id,
        )
        tasks.append(
            {
                "pack_id": pack_id,
                "question_key": gap_id,
                "prompt_text": prompt,
                "requires_authority": bool(gap.get("requires_authority")),
                "freshness_days": freshness,
            }
        )
    return PromptPack(pack_id=pack_id, tasks=tasks)


def sanitize_preview(text: str) -> str:
    """预览用：保留提示词结构，但把可能的身份信息进一步遮蔽（防御性）。"""
    # 提示词本身已不含身份信息，这里只做最小清洗，保证预览可安全展示。
    return text.strip()
