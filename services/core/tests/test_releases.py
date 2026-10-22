"""已发布 schema 制品指纹守卫（ADR-07、第 9.4 节）。

提示词与指标「已发布版本不得原地改」：schemas/manifest.yaml 登记了每个已发布文件的
sha256，这里断言磁盘内容与登记一致。改了已发布文件又忘了新建版本，这个测试会红。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from lifeos.config import AppConfig
from lifeos.releases import load_manifest, verify_releases


def test_manifest_is_not_empty() -> None:
    assert load_manifest(AppConfig().schemas_dir), "schemas/manifest.yaml 至少要登记已发布制品"


def test_released_artifacts_match_manifest() -> None:
    assert verify_releases(AppConfig().schemas_dir) == []


def test_crlf_checkout_still_verifies(tmp_path: Path) -> None:
    """同一份文件被 git 检出成 CRLF 时不该被当成「改过」——换行不是内容。"""
    content = b"# prompt\nline\n"
    digest = hashlib.sha256(content).hexdigest()
    (tmp_path / "manifest.yaml").write_text(
        f'released:\n  prompts/p.md: "{digest}"\n', encoding="utf-8"
    )
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "p.md").write_bytes(content.replace(b"\n", b"\r\n"))
    assert verify_releases(tmp_path) == []
