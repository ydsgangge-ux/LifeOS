"""成本估算（P1「成本记录」）：价格表放 config/llm_pricing.yaml，可随时改。

只做本地估算，用于在出站记录里看到一条请求的大致花费；真实账单以供应商为准。
表里没有的模型不猜价格，直接返回 None——cost_est 允许为空，宁可缺也不要编。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

_PER_1K = 1000.0


@dataclass(frozen=True)
class Pricing:
    """模型 → (每 1000 输入 token 价, 每 1000 输出 token 价)；缺失即未知。"""

    models: dict[str, tuple[float, float]]

    def rates(self, model: str) -> tuple[float, float] | None:
        return self.models.get(model)


def parse_pricing(raw: object) -> Pricing:
    models: dict[str, tuple[float, float]] = {}
    if isinstance(raw, dict):
        specs = raw.get("models")
        if isinstance(specs, dict):
            for name, spec in specs.items():
                if not isinstance(spec, dict):
                    continue
                models[str(name)] = (
                    float(spec.get("prompt_per_1k") or 0.0),
                    float(spec.get("completion_per_1k") or 0.0),
                )
    return Pricing(models=models)


@cache
def load_pricing(path: Path | str) -> Pricing:
    """读价格表；文件不存在视为「全部未知」，不报错也不猜。"""
    p = Path(path)
    if not p.is_file():
        return Pricing(models={})
    return parse_pricing(yaml.safe_load(p.read_text(encoding="utf-8")) or {})


def estimate_cost(
    pricing: Pricing, model: str, *, prompt_tokens: int, completion_tokens: int
) -> float | None:
    rates = pricing.rates(model)
    if rates is None:
        return None
    prompt_rate, completion_rate = rates
    return (prompt_tokens * prompt_rate + completion_tokens * completion_rate) / _PER_1K
