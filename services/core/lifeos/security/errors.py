"""security 层的异常类型。"""

from __future__ import annotations


class SecurityError(Exception):
    """安全层基类异常。"""


class InvalidPassphraseError(SecurityError):
    """口令或恢复密钥不正确（或密文被篡改）。"""


class VaultLockedError(SecurityError):
    """档案未解锁，禁止访问业务数据。"""
