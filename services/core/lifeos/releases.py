"""已发布 schema 制品的指纹校验（ADR-07、第 9.4 节）。

提示词与指标这类 schema 制品遵循「**已发布版本不得原地改，改动一律新建版本**」。
这条规矩不能只写在注释里：像迁移文件的 checksum 一样，只有能被校验才算数。

`schemas/manifest.yaml` 登记每个已发布文件的 sha256；本模块负责比对。
报告生成时把提示词版本、指标注册表版本与指纹写进 report（NFR-06）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

MANIFEST_NAME = "manifest.yaml"


def load_manifest(schemas_dir: str | Path) -> dict[str, str]:
    """读已发布制品清单：相对 schemas/ 的路径 → sha256。"""
    path = Path(schemas_dir) / MANIFEST_NAME
    if not path.exists():
        return {}
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    released = raw.get("released") if isinstance(raw, dict) else None
    if not isinstance(released, dict):
        return {}
    return {str(key): str(value) for key, value in released.items()}


def verify_releases(schemas_dir: str | Path) -> list[str]:
    """逐项校验清单里的指纹；返回问题清单（空列表表示全部一致）。"""
    base = Path(schemas_dir)
    problems: list[str] = []
    for rel, expected in sorted(load_manifest(base).items()):
        target = base / rel
        if not target.exists():
            problems.append(f"清单登记但文件缺失：{rel}")
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            problems.append(
                f"已发布文件被改动，应新建版本：{rel}"
                f"（清单 {expected[:12]}…，实际 {actual[:12]}…）"
            )
    return problems
