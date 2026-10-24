"""出站策略（第 7.4 节「出站策略」表）。

| 策略 | 含义 | 默认适用 |
| none | 完全不出站；报告走降级模式 | 儿童档、青少年档、新建档案 |
| api_summary | 只出站事实包中的聚合与代号化结果 | 成人、长者（开启后） |
| api_extended | 在 api_summary 基础上，出站本期条目标题、备注与时间戳 | 用户手动开启 |
"""

from __future__ import annotations

from .errors import PolicyDeniedError

POLICY_NONE = "none"
POLICY_SUMMARY = "api_summary"
POLICY_EXTENDED = "api_extended"

EGRESS_POLICIES = (POLICY_NONE, POLICY_SUMMARY, POLICY_EXTENDED)

# 儿童与青少年档不出站（第 7.7 节）；新建档案默认 none，要用户自己显式打开
LOCKED_AGE_BANDS = ("child", "teen")


def validate_policy(policy: str) -> str:
    if policy not in EGRESS_POLICIES:
        raise PolicyDeniedError(f"未知的出站策略：{policy}")
    return policy


def assert_policy_allowed(policy: str, age_band: str) -> None:
    """儿童与青少年档永远只能 none；其余档可以显式选择。"""
    validate_policy(policy)
    if age_band in LOCKED_AGE_BANDS and policy != POLICY_NONE:
        raise PolicyDeniedError(f"{age_band} 档固定为完全不出站")
