"""P6 W1.3：资源目录的三档回退（环境变量 > 随包目录 > 仓库根）。

打包后 ``Path(__file__).resolve().parents[3]`` 不再是仓库根，若不改这里的
路径推断，界面文案会全空、表单加载失败——而且不会给出清晰报错。本文件守住
三档回退的优先级，避免这个坑在打包时重现（验收 A1）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lifeos import config

RESOURCE_ENV = (
    "LIFEOS_SCHEMAS_DIR",
    "LIFEOS_CONFIG_DIR",
    "LIFEOS_I18N_DIR",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉资源环境变量，并确保拿掉可能残留的 PyInstaller 标记。"""
    for name in RESOURCE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


def test_env_vars_win_over_everything(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """第一档：显式环境变量最优先（启动器注入的就是这一档）。"""
    monkeypatch.setenv("LIFEOS_SCHEMAS_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("LIFEOS_CONFIG_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("LIFEOS_I18N_DIR", str(tmp_path / "i"))

    cfg = config.load_config(tmp_path)
    assert cfg.schemas_dir == tmp_path / "s"
    assert cfg.config_dir == tmp_path / "c"
    assert cfg.i18n_dir == tmp_path / "i"


def test_source_tree_falls_back_to_repo_root(clean_env: None) -> None:
    """第二档：源码运行时退回仓库根（开发与既有单测依赖这一档）。"""
    assert config.resource_root() == config._REPO_ROOT
    assert config.resolve_schemas_dir() == config._REPO_ROOT / "schemas"
    assert config.resolve_i18n_dir() == config._REPO_ROOT / "i18n"


def test_frozen_runtime_uses_bundled_resources(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """第三档：冻结运行时用随包的 resources/（打包后唯一可用的档位）。"""
    bundled = tmp_path / "resources"
    (bundled / "schemas").mkdir(parents=True)
    (bundled / "i18n").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert config.resource_root() == bundled
    assert config.resolve_schemas_dir() == bundled / "schemas"
    assert config.resolve_i18n_dir() == bundled / "i18n"


def test_frozen_without_resources_dir_still_points_into_bundle(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """冻结但没有 resources/ 时，仍指向随包内路径而不是随机的仓库根。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    root = config.resource_root()
    assert root == tmp_path / "resources"
    assert str(tmp_path) in str(root)
