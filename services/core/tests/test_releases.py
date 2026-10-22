"""已发布 schema 制品指纹守卫（ADR-07、第 9.4 节）。

提示词与指标「已发布版本不得原地改」：schemas/manifest.yaml 登记了每个已发布文件的
sha256，这里断言磁盘内容与登记一致。改了已发布文件又忘了新建版本，这个测试会红。
"""

from __future__ import annotations

from lifeos.config import AppConfig
from lifeos.releases import load_manifest, verify_releases


def test_manifest_is_not_empty() -> None:
    assert load_manifest(AppConfig().schemas_dir), "schemas/manifest.yaml 至少要登记已发布制品"


def test_released_artifacts_match_manifest() -> None:
    assert verify_releases(AppConfig().schemas_dir) == []
