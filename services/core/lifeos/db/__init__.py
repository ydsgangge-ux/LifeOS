"""数据库层：连接、迁移、仓储。

本模块允许依赖：security（第 3.2 节）。禁止：业务逻辑、联网。
"""

from .connection import VaultConnection, open_vault
from .registry import ProfileRecord, RegistryStore

__all__ = ["VaultConnection", "open_vault", "RegistryStore", "ProfileRecord"]
