"""提示词加载与沙箱渲染（第 9.4 节）。

提示词是**数据**而不是代码：放在 schemas/prompts/，文件名带版本（weekly.v1.md）。
文件分两段：YAML front matter（id、version、purpose、output_schema），以及用注释
标记切开的 system 与 user 正文。

三条硬要求都在这里落地：

1. template_hash = 文件内容的 sha256（第 9.4 节），写入 report 供复现；
   已发布的版本不得原地改，加载时取版本号最大的文件。
2. 模板用 Jinja2 沙箱渲染，只注入白名单变量——模板读不到数据库，也读不到
   事实包以外的对象（第 9.4 节最后一条）。
3. 未定义的变量直接报错（StrictUndefined），避免模型拿到半截提示词还以为正常。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from ..domain.errors import InvalidInputError, NotFoundError
from ..i18n import DEFAULT_LOCALE, t

# 只允许注入模板的变量名；其余一律不传（第 9.4 节「变量白名单」）
TEMPLATE_VARS = frozenset(
    {
        "period",
        "facts_yaml",
        "output_schema",
        "age_band",
        "language",
        "domain",
        "domain_label",
        "domain_lens",
    }
)

# 界面 locale → 提示词里的语言名（P6 W5b.3）：交给模型的是语言名，不是 locale 码。
# 语言名用该语言自身书写，与前端 LocalePicker 的选项名保持一致。
LANGUAGE_NAMES: dict[str, str] = {
    "zh-CN": "简体中文",
    "en": "English",
    "ja": "日本語",
    "es": "Español",
}


def language_name(locale: str) -> str:
    """把 locale 映射成语言名；未知 locale 回退默认语言（不把未知码丢给模型）。"""
    return LANGUAGE_NAMES.get(locale, LANGUAGE_NAMES[DEFAULT_LOCALE])


_SYSTEM_RE = re.compile(r"<!--\s*system\s*-->(.*?)<!--\s*/system\s*-->", re.S)
_USER_RE = re.compile(r"<!--\s*user\s*-->(.*?)<!--\s*/user\s*-->", re.S)
_VERSION_RE = re.compile(r"\.v(\d+)\.md$")

_ENV = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)


@dataclass(frozen=True)
class PromptTemplate:
    """一份已发布的提示词。hash 即文件内容 sha256。"""

    template_id: str
    version: int
    purpose: str
    output_schema: dict[str, Any]
    system: str
    user: str
    hash: str
    path: Path


@dataclass(frozen=True)
class DomainLens:
    """某个领域的分析镜头（schemas/prompts/domains/<domain>.yaml）。

    镜头同样是**数据**：它决定这个领域该问什么问题、看哪几个数、什么算噪声，
    以及不够数据时要不要调用模型。它是提示词的一部分，随 manifest 登记指纹。
    """

    domain: str
    question: str
    look_at: tuple[str, ...]
    worth_saying: tuple[str, ...]
    not_worth_saying: tuple[str, ...]
    traps: tuple[str, ...]
    min_data_note: str
    min_coverage_days: int | None
    min_plans_with_completion: int | None
    good_example: str
    # 只有「建议是否做得成」依赖用户处境的领域才拿基础信息；
    # 数值型领域（日常、开销、关系等）不需要，少发一份就少一份出站面。
    needs_basics: bool = False


def _split_front_matter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise InvalidInputError("提示词缺少 YAML front matter（首行应为 ---）")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise InvalidInputError("提示词 front matter 未闭合（缺少第二个 ---）")
    return "\n".join(lines[1:end]), "\n".join(lines[end + 1 :])


def _parse_template(prompt_id: str, version: int, path: Path, raw: str) -> PromptTemplate:
    front, body = _split_front_matter(raw)
    meta = yaml.safe_load(front) or {}
    if not isinstance(meta, dict):
        raise InvalidInputError(f"提示词 front matter 格式不合法：{path.name}")
    system_match = _SYSTEM_RE.search(body)
    user_match = _USER_RE.search(body)
    if system_match is None or user_match is None:
        raise InvalidInputError(f"提示词缺少 system/user 正文段：{path.name}")
    schema = meta.get("output_schema")
    if not isinstance(schema, dict):
        raise InvalidInputError(f"提示词 output_schema 缺失或格式非法：{path.name}")
    return PromptTemplate(
        template_id=str(meta.get("id") or prompt_id),
        version=int(meta.get("version", version)),
        purpose=str(meta.get("purpose") or ""),
        output_schema=schema,
        system=system_match.group(1).strip(),
        user=user_match.group(1).strip(),
        hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        path=path,
    )


def load_prompt(prompt_id: str, prompts_dir: Path | str) -> PromptTemplate:
    """加载某提示词的最新版本；目录里没有该提示词就报错，不静默降级。"""
    directory = Path(prompts_dir)
    candidates: list[tuple[int, Path]] = []
    for path in directory.glob(f"{prompt_id}.v*.md"):
        match = _VERSION_RE.search(path.name)
        if match:
            candidates.append((int(match.group(1)), path))
    if not candidates:
        raise NotFoundError(f"提示词不存在：{prompt_id}（{directory}）")
    version, path = max(candidates, key=lambda item: item[0])
    return _parse_template(prompt_id, version, path, path.read_text(encoding="utf-8"))


_LENS_FIELDS = (
    ("look_at", "看哪几个数，和什么比"),
    ("worth_saying", "什么变化值得说"),
    ("not_worth_saying", "什么不值得说"),
    ("traps", "常见解读陷阱"),
)


def _string_list(raw: Any, field: str, path: Path) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise InvalidInputError(f"领域镜头 {field} 必须是字符串数组：{path.name}")
    return tuple(item.strip() for item in raw if item.strip())


def _optional_int(raw: Any, field: str, path: Path) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"领域镜头 {field} 必须是整数：{path.name}") from exc


def _lens_version(path: Path, domain: str) -> int | None:
    """镜头文件名 → 版本；无版本后缀视为 v1，不符合命名规则的返回 None。"""
    if path.stem == domain:
        return 1
    match = re.fullmatch(rf"{re.escape(domain)}\.v(\d+)", path.stem)
    return int(match.group(1)) if match else None


def load_domain_lens(prompts_dir: Path | str, domain: str) -> DomainLens | None:
    """加载某个领域的最新版分析镜头；没有镜头文件时返回 None（沿用通用壳）。

    镜头同样受 ADR-07 约束：已发布的 `<domain>.yaml` 不再原地改，
    新版本写成 `<domain>.vN.yaml`，这里取版本号最大的文件。
    """
    directory = Path(prompts_dir) / "domains"
    candidates: list[tuple[int, Path]] = []
    for path in directory.glob(f"{domain}*.yaml"):
        version = _lens_version(path, domain)
        if version is not None:
            candidates.append((version, path))
    if not candidates:
        return None
    path = max(candidates, key=lambda item: item[0])[1]
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise InvalidInputError(f"领域镜头格式不合法：{path.name}")
    question = str(raw.get("question") or "").strip()
    example = str(raw.get("good_example") or "").strip()
    if not question or not example:
        raise InvalidInputError(f"领域镜头缺少 question 或 good_example：{path.name}")
    min_data = raw.get("min_data")
    min_data = min_data if isinstance(min_data, dict) else {}
    return DomainLens(
        domain=str(raw.get("domain") or domain),
        question=question,
        look_at=_string_list(raw.get("look_at"), "look_at", path),
        worth_saying=_string_list(raw.get("worth_saying"), "worth_saying", path),
        not_worth_saying=_string_list(
            raw.get("not_worth_saying"), "not_worth_saying", path
        ),
        traps=_string_list(raw.get("traps"), "traps", path),
        min_data_note=str(min_data.get("note") or "").strip(),
        min_coverage_days=_optional_int(
            min_data.get("coverage_days"), "min_data.coverage_days", path
        ),
        min_plans_with_completion=_optional_int(
            min_data.get("plans_with_completion"),
            "min_data.plans_with_completion",
            path,
        ),
        good_example=example,
        needs_basics=bool(raw.get("needs_basics", False)),
    )


def render_domain_lens(lens: DomainLens | None) -> str:
    """把镜头铺成注入模板的段落；没有镜头时返回空串，不留占位残迹。"""
    if lens is None:
        return ""
    lines = [f"【这个领域在回答什么】{lens.question}"]
    for field_name, title in _LENS_FIELDS:
        items: tuple[str, ...] = getattr(lens, field_name)
        if not items:
            continue
        lines.append(f"【{title}】")
        lines.extend(f"- {item}" for item in items)
    if lens.min_data_note:
        lines.append(f"【最低数据量】{lens.min_data_note}")
    lines.append("【一个好小结的示例】")
    lines.append(lens.good_example)
    return "\n".join(lines)


def _payload_locale(payload: dict[str, Any]) -> str:
    """从事实包的 profile 区块取 locale（P6 W5b.4：locale 随载荷一路传到出站）。"""
    profile = payload.get("profile")
    if isinstance(profile, dict):
        value = profile.get("locale")
        if isinstance(value, str) and value:
            return value
    return DEFAULT_LOCALE


def build_context(
    *,
    payload: dict[str, Any],
    output_schema: dict[str, Any],
    age_band: str,
    locale: str | None = None,
    domain: str | None = None,
    domain_lens: str = "",
) -> dict[str, Any]:
    """构造模板变量。只提供白名单变量，模板拿不到别的东西。
    ``domain`` 决定领域名，``domain_lens`` 是该领域分析镜头的渲染结果（见 domain.v3.md）。
    """
    period = payload.get("period")
    if not isinstance(period, dict):
        period = {}
    # privacy 已把日期折算成周序号（start_week/end_week）；这里统一成 start/end，
    # 让模板只认 {{ period.start }}，避免模板里出现两种字段名。
    normalized = {
        "kind": period.get("kind"),
        "start": period.get("start", period.get("start_week")),
        "end": period.get("end", period.get("end_week")),
    }
    resolved_locale = locale if locale is not None else _payload_locale(payload)
    return {
        "period": normalized,
        "facts_yaml": yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        "output_schema": json.dumps(output_schema, ensure_ascii=False, indent=2),
        "age_band": age_band,
        "language": language_name(resolved_locale),
        # 分领域 agent：给模型人类可读的领域名，代号只留在程序侧。
        "domain": domain or "",
        "domain_label": t(f"domain.{domain}", resolved_locale) if domain else "",
        "domain_lens": domain_lens,
    }


def render(template: str, context: dict[str, Any]) -> str:
    """沙箱渲染；未定义变量或模板语法错误直接抛错（前端会显示为生成失败）。"""
    safe = {key: value for key, value in context.items() if key in TEMPLATE_VARS}
    try:
        return _ENV.from_string(template).render(**safe)
    except Exception as exc:  # jinja2 的异常类型较碎，统一转成入参错误
        raise InvalidInputError(f"提示词渲染失败：{exc}") from exc


def render_prompt(template: PromptTemplate, context: dict[str, Any]) -> tuple[str, str]:
    return render(template.system, context), render(template.user, context)
