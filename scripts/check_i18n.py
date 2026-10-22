#!/usr/bin/env python
"""词表完整性门禁（P6 门禁 6.3、验收 A5）。

新增语言最省事的错法是「复制 zh-CN 再忘译」，或漏译几个键。漏一个键不会报错，
界面会**静默回退成中文**，于是出现中英混排——单看页面很难发现。这条门禁把它拦住：

1. **键集合一致**：每个 locale 的键集合必须与默认语言（zh-CN）完全相同，多了少了都报；
2. **占位符一致**：同一个键在各语言里的 ``{name}`` 必须一致，否则 ``str.format`` 会失配；
3. **无空值**：不许出现空字符串译文（往往意味着漏译）。

与 ``services/core/tests/test_locale.py`` 的同名断言**同源**：单测管开发期，本脚本管 CI 与
发布物（可直接对仓库根 ``i18n/`` 或 ``LIFEOS_I18N_DIR`` 指向的目录跑）。

用法（仓库根目录）::

    .venv\\Scripts\\python.exe scripts/check_i18n.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCALE = "zh-CN"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _i18n_dir() -> Path:
    override = os.environ.get("LIFEOS_I18N_DIR")
    if override:
        return Path(override)
    return _ROOT / "i18n"


def load_catalogs(directory: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out[path.stem] = {str(key): str(value) for key, value in raw.items()}
    return out


def check_catalogs(catalogs: dict[str, dict[str, str]]) -> list[str]:
    """返回问题清单；空列表代表通过。"""
    problems: list[str] = []
    if DEFAULT_LOCALE not in catalogs:
        problems.append(f"缺少默认语言词表 {DEFAULT_LOCALE}.yaml")
        return problems

    base = catalogs[DEFAULT_LOCALE]
    for key, text in base.items():
        if not text.strip():
            problems.append(f"{DEFAULT_LOCALE}:{key} 译文为空")

    for locale, catalog in catalogs.items():
        if locale == DEFAULT_LOCALE:
            continue
        missing = sorted(set(base) - set(catalog))
        extra = sorted(set(catalog) - set(base))
        if missing:
            problems.append(f"{locale} 缺少 {len(missing)} 个键：{missing[:10]}")
        if extra:
            problems.append(f"{locale} 多出 {len(extra)} 个键：{extra[:10]}")
        for key, text in catalog.items():
            if not text.strip():
                problems.append(f"{locale}:{key} 译文为空")
            if key not in base:
                continue
            want = set(_PLACEHOLDER.findall(base[key]))
            got = set(_PLACEHOLDER.findall(text))
            if got != want:
                problems.append(f"{locale}:{key} 占位符不一致：{sorted(want)} → {sorted(got)}")
    return problems


def main() -> int:
    directory = _i18n_dir()
    if not directory.is_dir():
        print(f"[check_i18n] 找不到词表目录：{directory}", file=sys.stderr)
        return 1

    catalogs = load_catalogs(directory)
    if not catalogs:
        print(f"[check_i18n] {directory} 下没有任何 *.yaml", file=sys.stderr)
        return 1

    problems = check_catalogs(catalogs)
    if problems:
        print("[check_i18n] 词表完整性检查未通过：", file=sys.stderr)
        for item in problems:
            print(f"  {item}", file=sys.stderr)
        return 1

    sizes = "，".join(f"{locale} {len(catalog)} 键" for locale, catalog in catalogs.items())
    print(f"[check_i18n] 通过：{sizes}（键集合、占位符、空值均一致）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
