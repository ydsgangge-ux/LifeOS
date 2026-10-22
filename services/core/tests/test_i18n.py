"""i18n 地基（NFR-08）：共享词表的读取、渲染，以及「schema 引用不悬空」的守卫。

词表在仓库根 i18n/，前后端同源；这里验证后端读取与渲染，并顺带检查
schemas/ 里写下的每一处 `i18n:<key>` 都能在词表里查到（防止两端漂移）。
"""

from __future__ import annotations

import yaml

from lifeos.config import AppConfig
from lifeos.i18n import DEFAULT_LOCALE, available_locales, message, render, t

_SCHEMAS_DIR = AppConfig().schemas_dir


def _i18n_refs(node: object) -> list[str]:
    refs: list[str] = []
    if isinstance(node, str):
        if node.startswith("i18n:"):
            refs.append(node.removeprefix("i18n:"))
    elif isinstance(node, dict):
        for value in node.values():
            refs.extend(_i18n_refs(value))
    elif isinstance(node, list):
        for item in node:
            refs.extend(_i18n_refs(item))
    return refs


def test_default_catalog_is_available():
    assert DEFAULT_LOCALE in available_locales()


def test_t_returns_text_and_formats_params():
    assert t("app.brand") == "LifeOS"
    assert t("diag.keep", cr="0.80", low=0.7) == "完成率 0.80 不低于 0.7，且产出趋势向好"


def test_t_falls_back_to_default_locale_then_key():
    # 还没有 en-US 词表：回退默认语言，而不是空白
    assert t("app.brand", "en-US") == "LifeOS"
    # 词表里也没有的键：原样返回，方便一眼看出遗漏
    assert t("no.such.key") == "no.such.key"


def test_tolerates_missing_params():
    # 少给参数时原样返回，不抛异常（宁可露出花括号，也不要 500）
    assert t("diag.retire") == "产出接近 0 已持续 {weeks} 周，且与目标关联弱或机会成本高"


def test_message_round_trip():
    msg = message("diag.retire", weeks=8)
    assert msg == {"key": "diag.retire", "params": {"weeks": 8}}
    assert render(msg) == "产出接近 0 已持续 8 周，且与目标关联弱或机会成本高"


def test_schema_i18n_refs_exist_in_catalog():
    missing: list[str] = []
    for path in sorted(_SCHEMAS_DIR.rglob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for key in _i18n_refs(raw):
            if t(key) == key:
                missing.append(f"{path.name}:{key}")
    assert missing == []
