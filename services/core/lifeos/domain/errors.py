"""领域层异常。"""

from __future__ import annotations

from ..security.errors import SecurityError


class DomainError(SecurityError):
    """领域层基类异常。"""


class NotFoundError(DomainError):
    """对象不存在。"""


class InvalidInputError(DomainError):
    """入参校验失败。"""


class NonEmptyRuleError(DomainError):
    """非空记录规则（第 4.1 节）：正式记录不能全空。"""


class DraftOnlyError(DomainError):
    """全空的记录只能是草稿。"""
