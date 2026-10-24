"""运行时配置（第 3.4 节数据目录、第 13.2 节 config.toml）。

数据目录 ~/.lifeos/；schemas/ 与 config/ 位于仓库根，可用环境变量覆盖
（LIFEOS_DATA_DIR / LIFEOS_SCHEMAS_DIR / LIFEOS_CONFIG_DIR），便于测试与打包。
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[3]  # services/core/lifeos/config.py → 仓库根


def _frozen_resource_root() -> Path | None:
    """PyInstaller 冻结运行时（P6 W1.2）的资源根。

    打包后 ``__file__`` 落在随包解压目录里，``parents[3]`` 会指到无关位置，
    因此优先用 PyInstaller 注入的 ``sys._MEIPASS`` 找随包的 ``resources/``。

    非冻结环境（源码运行、单测）返回 None，交回仓库根逻辑。
    """
    if not getattr(sys, "frozen", False):
        return None
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    root = Path(base)
    for candidate in (root / "resources", root):
        if (candidate / "schemas").is_dir() or (candidate / "i18n").is_dir():
            return candidate
    return root / "resources"


def resource_root() -> Path:
    """``schemas/`` / ``config/`` / ``i18n/`` 的父目录。

    三档回退（P6 W1.3）：环境变量（由各 ``resolve_*_dir`` 处理）>
    随包目录（冻结运行时）> 仓库根（源码开发）。打包后必须走第二档，
    否则界面文案全空、表单加载失败，且不会给出清晰报错。
    """
    return _frozen_resource_root() or _REPO_ROOT


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


def resolve_schemas_dir() -> Path:
    return _env_path("LIFEOS_SCHEMAS_DIR", resource_root() / "schemas")


def resolve_config_dir() -> Path:
    return _env_path("LIFEOS_CONFIG_DIR", resource_root() / "config")


def resolve_i18n_dir() -> Path:
    """共享词表目录（NFR-08：前端与后端同源）。"""
    return _env_path("LIFEOS_I18N_DIR", resource_root() / "i18n")


@dataclass
class AppConfig:
    data_dir: Path = field(
        default_factory=lambda: _env_path("LIFEOS_DATA_DIR", Path.home() / ".lifeos")
    )
    schemas_dir: Path = field(default_factory=resolve_schemas_dir)
    config_dir: Path = field(default_factory=resolve_config_dir)

    host: str = "127.0.0.1"
    idle_lock_minutes: int = 10

    argon2_memory_kib: int = 65536
    argon2_iterations: int = 3
    argon2_parallelism: int = 4

    llm_provider: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    # 输出长度只由 token 上限控制，不再在提示词里限制句数/字数。推理型模型的
    # 内部思考也记在这份预算里：8000 实测被思考吃掉七千多、正文 JSON 被截断，
    # 所以默认一万六，让正文拿到约八千。
    llm_max_tokens: int = 16000

    weekly_cron: str = "0 21 * * 0"
    monthly: str = "last_day"

    @property
    def forms_dir(self) -> Path:
        return self.schemas_dir / "forms"

    @property
    def dictionaries_dir(self) -> Path:
        return self.schemas_dir / "dictionaries"

    @property
    def metrics_dir(self) -> Path:
        return self.schemas_dir / "metrics"

    @property
    def egress_dir(self) -> Path:
        return self.schemas_dir / "egress"

    @property
    def prompts_dir(self) -> Path:
        return self.schemas_dir / "prompts"

    @property
    def research_templates_dir(self) -> Path:
        return self.schemas_dir / "research" / "templates"

    @property
    def diagnosis_config(self) -> Path:
        return self.config_dir / "diagnosis.yaml"

    @property
    def llm_pricing(self) -> Path:
        """成本估算价格表（P1 成本记录）；缺失即视为价格未知。"""
        return self.config_dir / "llm_pricing.yaml"

    @property
    def i18n_dir(self) -> Path:
        """共享词表目录（NFR-08：前端与后端同源）。"""
        return resolve_i18n_dir()


def load_config(data_dir: str | Path | None = None) -> AppConfig:
    """读取 config.toml（若存在）并合并到默认值。"""
    cfg = AppConfig()
    if data_dir is not None:
        cfg.data_dir = Path(data_dir)
    toml_path = cfg.data_dir / "config.toml"
    if not toml_path.exists():
        return cfg
    with toml_path.open("rb") as fh:
        raw = tomllib.load(fh)
    server = raw.get("server", {})
    security = raw.get("security", {})
    llm = raw.get("llm", {})
    reports = raw.get("reports", {})
    cfg.host = server.get("host", cfg.host)
    cfg.idle_lock_minutes = int(server.get("idle_lock_minutes", cfg.idle_lock_minutes))
    cfg.argon2_memory_kib = int(security.get("argon2_memory_kib", cfg.argon2_memory_kib))
    cfg.argon2_iterations = int(security.get("argon2_iterations", cfg.argon2_iterations))
    cfg.argon2_parallelism = int(security.get("argon2_parallelism", cfg.argon2_parallelism))
    cfg.llm_provider = llm.get("provider", cfg.llm_provider)
    cfg.llm_model = llm.get("model", cfg.llm_model)
    cfg.llm_base_url = llm.get("base_url", cfg.llm_base_url)
    cfg.llm_timeout_seconds = int(llm.get("timeout_seconds", cfg.llm_timeout_seconds))
    cfg.llm_max_retries = int(llm.get("max_retries", cfg.llm_max_retries))
    cfg.llm_max_tokens = int(llm.get("max_tokens", cfg.llm_max_tokens))
    cfg.weekly_cron = reports.get("weekly_cron", cfg.weekly_cron)
    cfg.monthly = reports.get("monthly", cfg.monthly)
    return cfg


# ---------------------------------------------------------------------------
# 写回 config.toml（只用于 [llm] 这类非敏感设置）
# ---------------------------------------------------------------------------
def _toml_value(value: Any) -> str:
    """标量/字符串数组的 TOML 字面量；config.toml 只会用到这几种类型。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


def _dump_table(prefix: str, values: dict[str, Any], lines: list[str]) -> None:
    scalars = {key: value for key, value in values.items() if not isinstance(value, dict)}
    children = {key: value for key, value in values.items() if isinstance(value, dict)}
    if lines and lines[-1] != "":
        lines.append("")
    if prefix:
        lines.append(f"[{prefix}]")
    for key, value in scalars.items():
        lines.append(f"{key} = {_toml_value(value)}")
    for key, child in children.items():
        _dump_table(f"{prefix}.{key}" if prefix else key, child, lines)


def _dump_config(raw: dict[str, Any]) -> str:
    lines: list[str] = []
    _dump_table("", raw, lines)
    return "\n".join(lines).strip() + "\n"


def save_llm_settings(
    config: AppConfig,
    *,
    provider: str,
    model: str,
    base_url: str,
    timeout_seconds: int,
    max_retries: int,
    max_tokens: int,
) -> None:
    """把非敏感 LLM 设置写回数据目录的 config.toml，并同步内存配置。

    只动 ``[llm]`` 段，其余段落原样保留；密钥绝不写进来（项目硬约束）。
    """
    path = config.data_dir / "config.toml"
    raw: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    llm = raw.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        raw["llm"] = llm
    llm.update(
        {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
            "max_tokens": max_tokens,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_dump_config(raw), encoding="utf-8")
    tmp.replace(path)

    # 内存里的同一份配置也要更新，否则新设置要等重启核心进程才生效
    config.llm_provider = provider
    config.llm_model = model
    config.llm_base_url = base_url
    config.llm_timeout_seconds = timeout_seconds
    config.llm_max_retries = max_retries
    config.llm_max_tokens = max_tokens
