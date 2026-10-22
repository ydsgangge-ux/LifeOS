"""隐私网关异常（第 7.4 节）。"""

from __future__ import annotations

from ..domain.errors import DomainError


class PrivacyError(DomainError):
    """隐私网关基类异常。"""


class UnknownPurposeError(PrivacyError):
    """用途没有对应白名单。

    第 7.5 节第 5 条要求这类缺口让构建失败，而不是悄悄放行——所以这里抛异常，
    黄金测试会为每个已知用途跑一遍完整流程。
    """


class PolicyDeniedError(PrivacyError):
    """当前出站策略不允许该内容出站。"""
