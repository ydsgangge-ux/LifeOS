"""analytics：确定性指标、趋势、相关性提示、计划诊断（第 8 章）。

只从 vault 计算事实包，不联网。唯一的写入（metric_cache）推迟到后续里程碑。
"""

from __future__ import annotations

from .service import AnalyticsService

__all__ = ["AnalyticsService"]
