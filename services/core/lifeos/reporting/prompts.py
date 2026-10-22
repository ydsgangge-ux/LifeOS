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
from ..i18n import DEFAULT_LOCALE

# 只允许注入模板的变量名；其余一律不传（第 9.4 节「变量白名单」）
TEMPLATE_VARS = frozenset({"period", "facts_yaml", "output_schema", "age_band", "language"})

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
) -> dict[str, Any]:
    """构造模板变量。只提供白名单变量，模板拿不到别的东西。"""
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
    return {
        "period": normalized,
        "facts_yaml": yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        "output_schema": json.dumps(output_schema, ensure_ascii=False, indent=2),
        "age_band": age_band,
        "language": language_name(locale if locale is not None else _payload_locale(payload)),
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
