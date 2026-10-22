"""reporting：报告流水线、输出校验器、降级模式、调度与补跑（第 9 章）。

对外只暴露三件事：

- ``generate_report``：按类型生成并落库一份报告（第 9.2 节七步流水线）。
- ``validate_output``：第 9.3 节八条规则的校验入口。
- ``ReportScheduler``：周报/月报定时与补跑（第 3.5 节 B）。

reading 侧的读写（list_reports / get_report）与阅读器行为（adopt_experiment）也在此导出。
"""

from .child_filter import build_child_report
from .degrade import build_degraded
from .pipeline import KIND_TO_PURPOSE, adopt_experiment, generate_report
from .prompts import LANGUAGE_NAMES, PromptTemplate, language_name, load_prompt
from .render import render_output, resolve_ref
from .scheduler import (
    ReportScheduler,
    due_periods,
    missing_periods,
    monthly_due_end,
    weekly_due_end,
)
from .store import get_report, list_reports, save_report
from .validator import STRENGTHS, ValidationResult, validate_output

__all__ = [
    "KIND_TO_PURPOSE",
    "LANGUAGE_NAMES",
    "STRENGTHS",
    "PromptTemplate",
    "ReportScheduler",
    "ValidationResult",
    "adopt_experiment",
    "build_child_report",
    "build_degraded",
    "due_periods",
    "generate_report",
    "get_report",
    "language_name",
    "list_reports",
    "load_prompt",
    "missing_periods",
    "monthly_due_end",
    "render_output",
    "resolve_ref",
    "save_report",
    "validate_output",
    "weekly_due_end",
]
