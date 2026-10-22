"""档案端点：列出、创建、解锁、锁定、改口令、销毁（第 12.2 节）。

档案操作只要求会话令牌，不要求解锁——解锁页正是要调用它们。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..domain import schema_defs as sd
from ..i18n import DEFAULT_LOCALE, available_locales
from ..security import keys
from ..security.errors import InvalidPassphraseError
from .deps import get_state, require_profile, require_token
from .errors import CODE_FORBIDDEN, CODE_INVALID_INPUT, CODE_LOCKED, ApiError
from .models import (
    ChangePassphraseRequest,
    DestroyRequest,
    GuardianPassphrase,
    GuardianPolicyUpdate,
    LocaleUpdate,
    ProfileCreateRequest,
    UnlockRequest,
)
from .state import AppState

router = APIRouter(tags=["profiles"], dependencies=[Depends(require_token)])

RECOVERY_KEY_NOTICE = (
    "恢复密钥只显示这一次，请立刻离线保存；口令与恢复密钥都丢失后数据不可恢复。"
)


def _owner_session_dek(state: AppState, profile_id: str) -> bytes:
    """要求「本人（owner）会话」解锁，返回其 DEK。

    挂/摘监护人密钥与调整可见范围都会改变监护人能读多少东西，只允许档案本人操作
    （第 7.7 节）；guardian 会话与 recovery 会话都可能由监护人一方掌握，不能放行。
    """
    if state.service() is None:
        raise ApiError(423, CODE_LOCKED, "档案未解锁")
    active = state.active
    if active is None:
        raise ApiError(423, CODE_LOCKED, "档案未解锁")
    if active.profile_id != profile_id:
        raise ApiError(400, CODE_INVALID_INPUT, "当前会话不属于该档案")
    if active.role != keys.ROLE_OWNER:
        raise ApiError(403, CODE_FORBIDDEN, "只有档案本人可以调整监护人设置")
    return active.dek_bytes()


@router.get("/profiles")
def list_profiles(state: AppState = Depends(get_state)) -> dict:
    active = state.active
    return {
        "items": [
            {
                "id": record.id,
                "alias": record.alias,
                "created_at": record.created_at,
                # 是否已挂监护人密钥（第 7.7 节）：前端据此决定是否显示监护人相关入口
                "guardian_linked": any(
                    w.get("role") == keys.ROLE_GUARDIAN for w in record.wrapped_keys
                ),
            }
            for record in state.profiles.registry.list_profiles()
        ],
        "active_profile_id": active.profile_id if active else None,
    }


@router.post("/profiles", status_code=201)
def create_profile(
    body: ProfileCreateRequest, state: AppState = Depends(get_state)
) -> dict:
    result = state.profiles.create(
        body.alias,
        body.passphrase,
        age_band=body.age_band,
        birth_year=body.birth_year,
        region_level=body.region_level,
        base_currency=body.base_currency,
        base_minor_exp=sd.minor_exp_for(body.base_currency),
        guardian_consent=body.guardian_consent,
        guardian_passphrase=body.guardian_passphrase,
    )
    return {
        "id": result.profile_id,
        "alias": result.alias,
        "recovery_key": result.recovery_key,
        "notice": RECOVERY_KEY_NOTICE,
    }


@router.post("/profiles/{profile_id}/unlock")
def unlock_profile(
    profile_id: str, body: UnlockRequest, state: AppState = Depends(get_state)
) -> dict:
    require_profile(state, profile_id)
    state.limiter.check(profile_id)

    try:
        if body.recovery_key:
            unlocked = state.profiles.unlock_with_recovery(profile_id, body.recovery_key)
        elif body.passphrase:
            if body.role == keys.ROLE_GUARDIAN:
                unlocked = state.profiles.unlock_as_guardian(profile_id, body.passphrase)
            else:
                unlocked = state.profiles.unlock(profile_id, body.passphrase)
        else:
            raise ApiError(
                400, CODE_INVALID_INPUT, "passphrase 与 recovery_key 必须提供一个"
            )
    except InvalidPassphraseError:
        state.limiter.fail(profile_id)
        raise

    state.limiter.reset(profile_id)
    state.unlock(unlocked)
    return {
        "profile_id": unlocked.profile_id,
        "alias": unlocked.alias,
        "age_band": unlocked.age_band,
        # 本次解锁用的角色：guardian 会话下读路径会按可见范围过滤（第 7.7 节）
        "role": unlocked.role,
        "guardian_linked": state.profiles.has_guardian(profile_id),
        # 档案自带的界面语言（NFR-08）；前端解锁后据此切 i18next。
        "locale": str(unlocked.meta.get("locale") or DEFAULT_LOCALE),
        # 出生年份（第 6.4 节）：前端据此精确判断是否已跨 14 岁以提示权限转移；
        # 早期档案或未填时为空，前端按缺失走启发式兜底（P6 W7）。
        "birth_year": unlocked.meta.get("birth_year"),
        "egress_policy": unlocked.egress_policy,
        "idle_lock_minutes": state.config.idle_lock_minutes,
    }


@router.post("/profiles/{profile_id}/guardian")
def add_guardian(
    profile_id: str,
    body: GuardianPassphrase,
    state: AppState = Depends(get_state),
) -> dict:
    """挂一枚监护人密钥包装（第 7.7 节）。需本人会话已解锁。"""
    require_profile(state, profile_id)
    dek = _owner_session_dek(state, profile_id)
    state.profiles.add_guardian(profile_id, dek, body.passphrase)
    return {"guardian_linked": True}


@router.delete("/profiles/{profile_id}/guardian")
def remove_guardian(profile_id: str, state: AppState = Depends(get_state)) -> dict:
    """撤销监护人密钥包装（满 14/18 周岁后本人可撤销，第 7.7 节）。"""
    require_profile(state, profile_id)
    _owner_session_dek(state, profile_id)
    state.profiles.remove_guardian(profile_id)
    return {"guardian_linked": False}


@router.get("/profiles/{profile_id}/guardian-policy")
def get_guardian_policy(profile_id: str, state: AppState = Depends(get_state)) -> dict:
    """读可见范围（未设置时为文档 7.7 的默认值）。"""
    require_profile(state, profile_id)
    dek = _owner_session_dek(state, profile_id)
    return {"policy": state.profiles.read_guardian_policy(profile_id, dek)}


@router.put("/profiles/{profile_id}/guardian-policy")
def put_guardian_policy(
    profile_id: str,
    body: GuardianPolicyUpdate,
    state: AppState = Depends(get_state),
) -> dict:
    """调整可见范围（第 7.7 节）：只允许档案本人操作，且 dream/relation 永不加白。"""
    require_profile(state, profile_id)
    dek = _owner_session_dek(state, profile_id)
    current = state.profiles.read_guardian_policy(profile_id, dek)
    if body.visible_domains is not None:
        current["visible_domains"] = body.visible_domains
    if body.allow_journal_text is not None:
        current["allow_journal_text"] = body.allow_journal_text
    if body.allow_detail is not None:
        current["allow_detail"] = body.allow_detail
    return {"policy": state.profiles.set_guardian_policy(profile_id, dek, current)}


@router.post("/profiles/{profile_id}/lock")
def lock_profile(profile_id: str, state: AppState = Depends(get_state)) -> dict:
    require_profile(state, profile_id)
    state.lock()
    return {"locked": True}


@router.put("/profiles/{profile_id}/locale")
def put_locale(
    profile_id: str, body: LocaleUpdate, state: AppState = Depends(get_state)
) -> dict:
    """记录档案的界面语言（NFR-08、P6 W4）。

    语言是档案自己的属性（不是某次会话的属性），所以只允许「本人会话」改：
    监护人会话改的是孩子档案的口径，容易越界；语言码须在可用词表之内。
    """
    require_profile(state, profile_id)
    if state.service() is None or state.active is None:
        raise ApiError(423, CODE_LOCKED, "档案未解锁")
    active = state.active
    if active.profile_id != profile_id:
        raise ApiError(400, CODE_INVALID_INPUT, "当前会话不属于该档案")
    if active.role != keys.ROLE_OWNER:
        raise ApiError(403, CODE_FORBIDDEN, "只有档案本人可以调整界面语言")
    if body.locale not in available_locales():
        raise ApiError(400, CODE_INVALID_INPUT, f"不支持的语言：{body.locale}")
    return {"locale": state.profiles.set_locale(profile_id, active.dek_bytes(), body.locale)}


@router.post("/profiles/{profile_id}/change-passphrase")
def change_passphrase(
    profile_id: str,
    body: ChangePassphraseRequest,
    state: AppState = Depends(get_state),
) -> dict:
    require_profile(state, profile_id)
    # 只用新 KEK 重新包装 DEK，不重写数据库（第 4.6 节），当前连接保持有效
    state.profiles.change_passphrase(profile_id, body.old, body.new)
    return {"changed": True}


@router.post("/profiles/{profile_id}/destroy")
def destroy_profile(
    profile_id: str, body: DestroyRequest, state: AppState = Depends(get_state)
) -> dict:
    require_profile(state, profile_id)
    state.lock()  # crypto-shred 前先关掉连接，避免文件被占用
    state.profiles.destroy(profile_id, body.confirm_alias)
    return {"destroyed": True}
