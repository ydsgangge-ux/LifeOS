"""backup：加密备份与恢复（FR-17、第 7.6 节）。

对外暴露三件事：``create_backup``（打一份加密备份）、``restore_backup``（用口令或
恢复密钥恢复）、``list_backups``（列出默认目录下的备份）。归档格式见 archive.py。
"""

from .archive import BACKUP_SUFFIX, BackupError, read_header
from .service import (
    BACKUP_KEEP,
    BackupResult,
    RestoreResult,
    backups_dir,
    create_backup,
    list_backups,
    restore_backup,
)

__all__ = [
    "BACKUP_KEEP",
    "BACKUP_SUFFIX",
    "BackupError",
    "BackupResult",
    "RestoreResult",
    "backups_dir",
    "create_backup",
    "list_backups",
    "read_header",
    "restore_backup",
]
