"""国际化（NFR-08）：默认 zh-CN，所有面向用户的文案走 i18n key。

文案集中在仓库根 i18n/<locale>.yaml（与前端同源，做法同 schemas/dictionaries，
避免两端漂移）。本模块只负责「读词表」与「渲染」：

- 面向用户的生成内容（诊断理由、相关性提示、事实包缺口等）在 API 里以
  ``message(key, **params)`` 形式返回，即 ``{"key": ..., "params": {...}}``，
  由渲染层（前端 i18next 或报告模板）按当前语言本地化。
- 服务端需要直接出中文/成品的场景（报告、日志）用 ``render(msg, locale)``。

新增语言：在 i18n/ 放 <locale>.yaml 即可，无需改代码。
"""

from __future__ import annotations

from functools import cache
from typing import Any

import yaml

from .config import resolve_i18n_dir

DEFAULT_LOCALE = "zh-CN"
# 词表目录走与 config 同一套「环境变量 > 随包目录 > 仓库根」回退（P6 W1.3）：
# 打包后 i18n/ 随包分发，不再能靠 __file__ 反推仓库根。
_I18N_DIR = resolve_i18n_dir()


@cache
def _catalog(locale: str) -> dict[str, str]:
    path = _I18N_DIR / f"{locale}.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return {str(key): str(value) for key, value in raw.items()}


def available_locales() -> list[str]:
    if not _I18N_DIR.exists():
        return [DEFAULT_LOCALE]
    return sorted(path.stem for path in _I18N_DIR.glob("*.yaml"))


def t(key: str, locale: str = DEFAULT_LOCALE, **params: Any) -> str:
    """按 key 取文案并填充 {name} 参数；缺失时回退默认语言，再回退为 key 本身。"""
    text = _catalog(locale).get(key)
    if text is None and locale != DEFAULT_LOCALE:
        text = _catalog(DEFAULT_LOCALE).get(key)
    if text is None:
        return key
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        return text


def message(key: str, **params: Any) -> dict[str, Any]:
    """构造一个「可本地化的消息」对象，供 API 返回、由渲染层翻译。"""
    return {"key": key, "params": params}


def render(msg: dict[str, Any], locale: str = DEFAULT_LOCALE) -> str:
    """把消息对象渲染成指定语言的纯文本（报告/日志等场景）。"""
    params = msg.get("params") or {}
    return t(str(msg.get("key", "")), locale, **params)


def resolve_ref(value: Any, locale: str = DEFAULT_LOCALE) -> str:
    """解析数据层的 ``i18n:<key>`` 引用（表单标题、指标说明、字典 label）。

    不是引用（不以 ``i18n:`` 开头）就原样返回，便于同一个函数处理两类数据。
    """
    text = str(value)
    prefix = "i18n:"
    if text.startswith(prefix):
        return t(text[len(prefix) :], locale)
    return text
