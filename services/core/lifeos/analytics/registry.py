"""指标注册表（第 8.2 节）：从 schemas/metrics/*.yaml 加载指标定义。

指标一律在 YAML 里声明，不在代码里硬编码。加载器支持两种文件形态：
顶层是列表（第 8.2 节的示例），或 `{version, metrics}` 字典（便于版本管理）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..domain.errors import InvalidInputError

AGG_KINDS = frozenset(("mean", "sum", "std"))
VALUE_KINDS = frozenset(("number", "money", "ratio"))


@dataclass(frozen=True)
class MetricDef:
    key: str
    domain: str
    description: str
    unit: str | None = None
    grain: str = "day"
    agg: str | None = None
    source_table: str | None = None
    source_column: str | None = None
    formula: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    row_filter: dict[str, Any] = field(default_factory=dict)
    value_kind: str = "number"
    exclude_anomalies: bool = False
    min_n: int = 1

    @property
    def is_formula(self) -> bool:
        return self.formula is not None

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "domain": self.domain,
            "description": self.description,
            "unit": self.unit,
            "grain": self.grain,
            "source": (
                f"{self.source_table}.{self.source_column}" if self.source_table else None
            ),
            "agg": self.agg,
            "formula": self.formula,
            "value_kind": self.value_kind,
            "exclude_anomalies": self.exclude_anomalies,
            "min_n": self.min_n,
        }


def _parse_source(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    if "." not in raw:
        raise InvalidInputError(f"指标 source 必须形如 表.列：{raw}")
    table, column = raw.split(".", 1)
    return table, column


def _parse_metric(raw: dict[str, Any], source: Path) -> MetricDef:
    key = raw.get("key")
    if not key:
        raise InvalidInputError(f"指标缺少 key：{source.name}")
    agg = raw.get("agg")
    formula = raw.get("formula")
    table, column = _parse_source(raw.get("source"))
    if not formula and not table:
        raise InvalidInputError(f"指标 {key} 必须声明 source 或 formula")
    if formula and agg:
        raise InvalidInputError(f"指标 {key} 不能同时声明 source/agg 与 formula")
    if agg is not None and agg not in AGG_KINDS:
        raise InvalidInputError(f"指标 {key} 的 agg 未知：{agg}")
    value_kind = raw.get("value_kind", "number")
    if value_kind not in VALUE_KINDS:
        raise InvalidInputError(f"指标 {key} 的 value_kind 未知：{value_kind}")
    return MetricDef(
        key=str(key),
        domain=str(raw.get("domain", "")),
        description=str(raw.get("description", key)),
        unit=raw.get("unit"),
        grain=str(raw.get("grain", "day")),
        agg=agg,
        source_table=table,
        source_column=column,
        formula=formula,
        params=dict(raw.get("params") or {}),
        row_filter=dict(raw.get("filter") or {}),
        value_kind=value_kind,
        exclude_anomalies=bool(raw.get("exclude_anomalies", False)),
        min_n=int(raw.get("min_n", 1)),
    )


def _iter_metric_dicts(doc: Any, source: Path) -> list[dict[str, Any]]:
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        items = doc.get("metrics")
        if isinstance(items, list):
            return items
    raise InvalidInputError(f"指标文件格式无法识别：{source.name}")


class MetricRegistry:
    """加载并索引 schemas/metrics 下的指标定义。"""

    def __init__(self, metrics_dir: str | Path) -> None:
        self.metrics_dir = Path(metrics_dir)
        self._metrics: dict[str, MetricDef] = {}
        self._versions: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        self._metrics.clear()
        self._versions.clear()
        if not self.metrics_dir.exists():
            return
        for path in sorted(self.metrics_dir.glob("*.yaml")):
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and doc.get("version"):
                self._versions[path.name] = str(doc["version"])
            for raw in _iter_metric_dicts(doc, path):
                metric = _parse_metric(raw, path)
                self._metrics[metric.key] = metric

    def get(self, key: str) -> MetricDef:
        if key not in self._metrics:
            raise InvalidInputError(f"指标不存在：{key}")
        return self._metrics[key]

    def keys(self) -> list[str]:
        return list(self._metrics)

    def all(self) -> list[MetricDef]:
        return list(self._metrics.values())

    def versions(self) -> dict[str, str]:
        return dict(self._versions)

    def fingerprint(self) -> dict[str, str]:
        """指标注册表的版本与内容指纹，写进报告供复现（NFR-06）。

        版本号是各文件 front matter 里的 ``version`` 字段，供人读；
        指纹是全部指标文件按文件名排序后的内容 sha256，供校验「有没有被原地改」。
        """
        digest = hashlib.sha256()
        if self.metrics_dir.exists():
            for path in sorted(self.metrics_dir.glob("*.yaml")):
                digest.update(path.name.encode("utf-8"))
                digest.update(b"\0")
                digest.update(path.read_bytes())
                digest.update(b"\0")
        version = ";".join(f"{name}={ver}" for name, ver in sorted(self._versions.items()))
        return {
            "version": version or "metrics_unversioned",
            "hash": digest.hexdigest(),
        }
