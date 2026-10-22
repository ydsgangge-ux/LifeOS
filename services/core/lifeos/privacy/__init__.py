"""privacy：隐私网关——选择、白名单、泛化、预览、审计（第 7.4 节）。

所有出站内容都要过这条管线；泄漏黄金测试是它的验收手段（第 7.5 节）。
"""

from .audit import (
    list_egress_log,
    mark_egress,
    payload_digest,
    read_age_band,
    read_anchor,
    read_policy,
    record_egress,
    write_policy,
)
from .errors import PolicyDeniedError, PrivacyError, UnknownPurposeError
from .gateway import Prepared, monthly_income_value, prepare
from .generalize import person_names, relative_money, truncate, week_index
from .policy import (
    EGRESS_POLICIES,
    LOCKED_AGE_BANDS,
    POLICY_EXTENDED,
    POLICY_NONE,
    POLICY_SUMMARY,
    assert_policy_allowed,
    validate_policy,
)
from .scan import ScanReport, scan_text
from .whitelist import EGRESS_PURPOSES, Whitelist, available_purposes, load_whitelist

__all__ = [
    "EGRESS_POLICIES",
    "EGRESS_PURPOSES",
    "LOCKED_AGE_BANDS",
    "POLICY_EXTENDED",
    "POLICY_NONE",
    "POLICY_SUMMARY",
    "PolicyDeniedError",
    "Prepared",
    "PrivacyError",
    "ScanReport",
    "UnknownPurposeError",
    "Whitelist",
    "assert_policy_allowed",
    "available_purposes",
    "list_egress_log",
    "load_whitelist",
    "mark_egress",
    "monthly_income_value",
    "payload_digest",
    "person_names",
    "prepare",
    "read_age_band",
    "read_anchor",
    "read_policy",
    "record_egress",
    "relative_money",
    "scan_text",
    "truncate",
    "validate_policy",
    "week_index",
    "write_policy",
]
