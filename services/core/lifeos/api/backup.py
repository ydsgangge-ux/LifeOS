"""备份与恢复端点（第 7.6 节、第 12.2 节、FR-17）。

- GET  /backups：列出当前档案默认备份目录下的备份。
- POST /backup：把当前档案打成一份加密备份（需要已解锁，才能做一致快照）。
- POST /restore：用口令或恢复密钥恢复一份备份。恢复会覆盖档案目录，故要求输入
  档案别名二次确认；执行前先把当前会话锁定，避免还开着连接时覆盖文件。

恢复端点只要求会话令牌、不要求解锁——archive 丢失 registry 时，恢复正是要
在「还没解锁」的状态下把档案先还原回去。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..backup import create_backup, list_backups, read_header, restore_backup
from ..domain.service import DomainService
from ..i18n import t
from .deps import get_state, require_service, require_token
from .errors import CODE_INVALID_INPUT, CODE_LOCKED, ApiError
from .models import BackupRequest, RestoreRequest
from .state import AppState

router = APIRouter(tags=["backup"])


@router.get("/backups")
def list_backups_endpoint(
    state: AppState = Depends(get_state), _: None = Depends(require_token)
) -> dict:
    active = state.active
    if active is None:
        raise ApiError(423, CODE_LOCKED, t("backup.locked"))
    return {"items": list_backups(state.profiles, active.profile_id)}


@router.post("/backup")
def create_backup_endpoint(
    body: BackupRequest,
    state: AppState = Depends(get_state),
    service: DomainService = Depends(require_service),
) -> dict:
    active = state.active
    if active is None:
        raise ApiError(423, CODE_LOCKED, t("backup.locked"))
    if body.dest_dir is not None and not body.dest_dir.strip():
        raise ApiError(400, CODE_INVALID_INPUT, t("backup.dest_required"))
    result = create_backup(
        state.profiles, active.profile_id, service.conn, active.dek_bytes(), dest_dir=body.dest_dir
    )
    return result.to_json()


@router.post("/restore")
def restore_backup_endpoint(
    body: RestoreRequest,
    state: AppState = Depends(get_state),
    _: None = Depends(require_token),
) -> dict:
    if not body.passphrase and not body.recovery_key:
        raise ApiError(400, CODE_INVALID_INPUT, t("backup.need_credential"))

    header = read_header(body.path)  # 文件缺失/格式错误 → BackupError → 400
    alias = str(header.meta.get("alias") or "")
    if body.confirm_alias.strip() != alias:
        raise ApiError(400, CODE_INVALID_INPUT, t("backup.confirm_mismatch"))

    state.lock()  # 覆盖档案目录前先断开当前连接
    result = restore_backup(
        state.profiles,
        body.path,
        passphrase=body.passphrase,
        recovery_key=body.recovery_key,
    )
    return result.to_json()
