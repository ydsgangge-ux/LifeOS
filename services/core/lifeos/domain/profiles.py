"""档案与保险库生命周期（FR-01）。

- 创建档案：生成 DEK、派生 KEK、包装密钥（owner + guardian + recovery）、建 vault、跑迁移。
- 解锁：读 registry，派生 KEK，拆封 DEK，打开 vault。
- 改口令：只用新 KEK 重新包装 DEK（第 4.6 节）。
- 销毁（crypto-shred）：先删 registry 中的 wrapped_keys，再删 vault 目录（第 7.2 节）。
- 监护人（第 7.7 节）：guardian 包装、可见范围策略、同意记录。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..db.connection import open_vault
from ..db.migrator import migrate
from ..db.registry import ProfileRecord, RegistryStore
from ..i18n import message, render
from ..privacy.policy import LOCKED_AGE_BANDS, POLICY_NONE
from ..security import keys
from ..security.errors import InvalidPassphraseError, SecurityError
from ..security.kdf import (
    DEFAULT_ITERATIONS,
    DEFAULT_MEMORY_KIB,
    DEFAULT_PARALLELISM,
    KdfParams,
    derive_kek,
    new_salt,
)
from ..util.ids import new_ulid, now_utc_iso
from . import guardian as guardian_mod

CONSENT_AGE = 14  # 法规敏感线：未满 14 周岁需要监护人同意（第 2.1 节）
AGE_BANDS = ("child", "teen", "adult", "senior")

# 解锁角色：owner / guardian / recovery（第 7.2 节第 3 条）
UNLOCK_ROLES = (keys.ROLE_OWNER, keys.ROLE_GUARDIAN, keys.ROLE_RECOVERY)


class ProfileError(SecurityError):
    """档案操作错误。"""


class GuardianConsentError(ProfileError):
    """未满 14 周岁创建档案却没有勾选监护人同意（第 7.7 节）。"""

    def __init__(self) -> None:
        self.message: dict[str, Any] = message("guardian.consent_required")
        super().__init__(render(self.message))


@dataclass
class CreateResult:
    profile_id: str
    alias: str
    recovery_key: str  # 只返回一次，界面必须提示用户自行保管


@dataclass
class UnlockedProfile:
    profile_id: str
    alias: str
    dek: bytes = field(repr=False)
    meta: dict[str, object] = field(default_factory=dict)
    role: str = keys.ROLE_OWNER

    @property
    def age_band(self) -> str:
        return str(self.meta.get("age_band", "adult"))

    @property
    def egress_policy(self) -> str:
        return str(self.meta.get("egress_policy", "none"))


def derive_age_band(birth_year: int | None, today: date | None = None) -> str:
    if birth_year is None:
        return "adult"
    age = (today or date.today()).year - birth_year
    if age <= 11:
        return "child"
    if age <= 17:
        return "teen"
    if age <= 59:
        return "adult"
    return "senior"


def consent_required_for(birth_year: int | None, today: date | None = None) -> bool:
    if birth_year is None:
        return False
    return (today or date.today()).year - birth_year < CONSENT_AGE


def _new_kdf_params() -> KdfParams:
    """一套新的 Argon2id 参数（每次换新盐：每个角色各用自己的盐派生 KEK）。"""
    return KdfParams(
        m=DEFAULT_MEMORY_KIB, t=DEFAULT_ITERATIONS, p=DEFAULT_PARALLELISM, salt=new_salt()
    )


class ProfileService:
    """档案与保险库的生命周期管理。"""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.vaults_dir = self.data_dir / "vaults"
        # 备份刻意放在 vaults/ 之外：档案目录被毁时备份本身不能跟着一起没了
        self.backups_dir = self.data_dir / "backups"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.vaults_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.registry = RegistryStore(self.data_dir / "registry.db")

    # -- 路径 -------------------------------------------------------------
    def vault_path(self, profile_id: str) -> Path:
        return self.vaults_dir / profile_id / "vault.db"

    def blobs_dir(self, profile_id: str) -> Path:
        return self.vaults_dir / profile_id / "blobs"

    def backup_dir(self, profile_id: str) -> Path:
        return self.backups_dir / profile_id

    # -- 创建 -------------------------------------------------------------
    def create(
        self,
        alias: str,
        passphrase: str,
        *,
        age_band: str | None = None,
        birth_year: int | None = None,
        region_level: str | None = None,
        base_currency: str = "CNY",
        base_minor_exp: int = 2,
        egress_policy: str = "none",
        guardian_passphrase: str | None = None,
        guardian_consent: bool = False,
    ) -> CreateResult:
        if not alias.strip():
            raise ProfileError("档案别名不能为空")
        if len(passphrase) < 6:
            raise ProfileError("口令至少 6 个字符")
        if guardian_passphrase is not None and len(guardian_passphrase) < 6:
            raise ProfileError("监护人口令至少 6 个字符")

        band = age_band or derive_age_band(birth_year)
        if band not in AGE_BANDS:
            raise ProfileError(f"未知的年龄档：{band}")

        # 第 7.7 节：未满 14 周岁（child 档，或出生年份推算未满 14）必须监护人确认。
        # child 档即使没填 birth_year 也要同意，否则 consent_required 会被算成 0。
        needs_consent = band == "child" or consent_required_for(birth_year)
        if needs_consent and not guardian_consent:
            raise GuardianConsentError()

        # 儿童/青少年档创建时即锁死不出站（第 7.7 节），调用方传了别的值也一律覆盖
        if band in LOCKED_AGE_BANDS:
            egress_policy = POLICY_NONE

        profile_id = new_ulid()
        dek = keys.new_dek()
        kdf_params = _new_kdf_params()
        kek = derive_kek(passphrase, kdf_params)

        # 每个角色各包装一份（第 7.2 节第 3 条）：owner 与 guardian 各用自己的盐
        # 派生 KEK、参数随条目一起存；recovery 直接用高熵密钥，不带 kdf_params。
        owner_wrap = keys.wrap_dek(
            dek, kek, keys.ROLE_OWNER, kdf_params=kdf_params.to_json()
        )
        recovery_key = keys.new_recovery_key()
        recovery_wrap = keys.wrap_dek(dek, recovery_key, keys.ROLE_RECOVERY)
        wrapped: list[dict[str, Any]] = [
            owner_wrap.to_json(),
            recovery_wrap.to_json(),
        ]
        if guardian_passphrase is not None:
            wrapped.append(self._wrap_guardian(dek, guardian_passphrase))

        # 先建目录与 registry 记录，再建库并写 profile_meta
        self.blobs_dir(profile_id).mkdir(parents=True, exist_ok=True)
        self.registry.add(
            ProfileRecord(
                id=profile_id,
                alias=alias,
                vault_path=str(self.vaults_dir / profile_id),
                kdf_params=kdf_params.to_json(),
                wrapped_keys=wrapped,
                created_at=now_utc_iso(),
            )
        )

        try:
            conn = open_vault(self.vault_path(profile_id), dek)
            try:
                migrate(conn, self.vault_path(profile_id))
                with conn.transaction() as tx:
                    tx.execute(
                        """
                        INSERT INTO profile_meta
                          (id, alias, birth_year, age_band, consent_required, consent_at,
                           locale, region_level, base_currency, base_minor_exp,
                           modules_json, egress_policy, created_at)
                        VALUES (1, ?, ?, ?, ?, ?, 'zh-CN', ?, ?, ?, '{}', ?, ?)
                        """,
                        (
                            alias,
                            birth_year,
                            band,
                            1 if needs_consent else 0,
                            now_utc_iso() if needs_consent else None,
                            region_level,
                            base_currency,
                            base_minor_exp,
                            egress_policy,
                            now_utc_iso(),
                        ),
                    )
            finally:
                conn.close()
        except Exception:
            # 创建失败要回滚掉 registry 与目录，避免留下半成品档案
            self.registry.remove(profile_id)
            shutil.rmtree(self.vaults_dir / profile_id, ignore_errors=True)
            raise

        return CreateResult(
            profile_id=profile_id,
            alias=alias,
            recovery_key=keys.format_recovery_key(recovery_key),
        )

    # -- 解锁 / 锁定 ------------------------------------------------------
    def unlock(self, profile_id: str, passphrase: str) -> UnlockedProfile:
        return self._unlock_with_passphrase(profile_id, passphrase, keys.ROLE_OWNER)

    def unlock_as_guardian(self, profile_id: str, passphrase: str) -> UnlockedProfile:
        """以监护人口令解锁（第 7.7 节）；没挂 guardian 包装时报 ProfileError。"""
        return self._unlock_with_passphrase(profile_id, passphrase, keys.ROLE_GUARDIAN)

    def unlock_with_recovery(self, profile_id: str, recovery_key_text: str) -> UnlockedProfile:
        record = self._require(profile_id)
        key = keys.parse_recovery_key(recovery_key_text)
        dek = self._unwrap_role(record, keys.ROLE_RECOVERY, key)
        return self._open(record, dek, keys.ROLE_RECOVERY)

    def _unlock_with_passphrase(
        self, profile_id: str, passphrase: str, role: str
    ) -> UnlockedProfile:
        record = self._require(profile_id)
        params = self._role_kdf_params(record, role)
        dek = self._unwrap_role(record, role, derive_kek(passphrase, params))
        return self._open(record, dek, role)

    def read_meta(self, profile_id: str, dek: bytes) -> dict[str, object]:
        conn = open_vault(self.vault_path(profile_id), dek)
        try:
            row = conn.query_one("SELECT * FROM profile_meta WHERE id = 1")
            return {} if row is None else dict(row)
        finally:
            conn.close()

    # -- 改口令 -----------------------------------------------------------
    def change_passphrase(self, profile_id: str, old: str, new: str) -> None:
        if len(new) < 6:
            raise ProfileError("新口令至少 6 个字符")
        record = self._require(profile_id)
        dek = self._unwrap_role(
            record,
            keys.ROLE_OWNER,
            derive_kek(old, self._role_kdf_params(record, keys.ROLE_OWNER)),
        )

        new_kdf = _new_kdf_params()
        new_kek = derive_kek(new, new_kdf)

        # 只替换 owner 包装（连同它自己的 KDF 参数），保留 guardian / recovery
        new_wrapped: list[dict[str, Any]] = [
            keys.wrap_dek(
                dek, new_kek, keys.ROLE_OWNER, kdf_params=new_kdf.to_json()
            ).to_json()
        ]
        new_wrapped += [w for w in record.wrapped_keys if w.get("role") != keys.ROLE_OWNER]
        self.registry.update_keys(profile_id, new_kdf.to_json(), new_wrapped)

    # -- 界面语言（NFR-08、P6 W4）------------------------------------------
    def set_locale(self, profile_id: str, dek: bytes, locale: str) -> str:
        """把界面语言写进 ``profile_meta.locale``。

        列随 0001_init 就存在（默认 'zh-CN'），因此不需要新迁移；解锁时读回来即可。
        """
        conn = open_vault(self.vault_path(profile_id), dek)
        try:
            with conn.transaction() as tx:
                tx.execute("UPDATE profile_meta SET locale = ? WHERE id = 1", (locale,))
        finally:
            conn.close()
        return locale

    # -- 监护人（第 7.7 节）----------------------------------------------
    def has_guardian(self, profile_id: str) -> bool:
        record = self._require(profile_id)
        return any(w.get("role") == keys.ROLE_GUARDIAN for w in record.wrapped_keys)

    def add_guardian(self, profile_id: str, dek: bytes, passphrase: str) -> None:
        """挂一枚 guardian 包装；调用方须已持 DEK（owner / 恢复会话）。"""
        if len(passphrase) < 6:
            raise ProfileError("监护人口令至少 6 个字符")
        record = self._require(profile_id)
        wrapped = [w for w in record.wrapped_keys if w.get("role") != keys.ROLE_GUARDIAN]
        wrapped.append(self._wrap_guardian(dek, passphrase))
        self.registry.update_keys(profile_id, record.kdf_params, wrapped)

    def remove_guardian(self, profile_id: str) -> None:
        """撤销 guardian 包装（满 14/18 周岁后本人可撤销，第 7.7 节）。"""
        record = self._require(profile_id)
        if not any(w.get("role") == keys.ROLE_GUARDIAN for w in record.wrapped_keys):
            raise ProfileError("该档案没有监护人包装")
        wrapped = [w for w in record.wrapped_keys if w.get("role") != keys.ROLE_GUARDIAN]
        self.registry.update_keys(profile_id, record.kdf_params, wrapped)

    def read_guardian_policy(self, profile_id: str, dek: bytes) -> dict[str, Any]:
        raw = self.read_meta(profile_id, dek).get("guardian_policy_json")
        parsed = json.loads(raw) if isinstance(raw, str) and raw else None
        return guardian_mod.GuardianPolicy.from_json(parsed).to_json()

    def set_guardian_policy(
        self, profile_id: str, dek: bytes, policy: dict[str, Any] | None
    ) -> dict[str, Any]:
        normalized = guardian_mod.GuardianPolicy.from_json(policy).to_json()
        conn = open_vault(self.vault_path(profile_id), dek)
        try:
            with conn.transaction() as tx:
                tx.execute(
                    "UPDATE profile_meta SET guardian_policy_json = ? WHERE id = 1",
                    (json.dumps(normalized, ensure_ascii=False),),
                )
        finally:
            conn.close()
        return normalized

    def _wrap_guardian(self, dek: bytes, passphrase: str) -> dict[str, Any]:
        params = _new_kdf_params()
        return keys.wrap_dek(
            dek,
            derive_kek(passphrase, params),
            keys.ROLE_GUARDIAN,
            kdf_params=params.to_json(),
        ).to_json()

    # -- 销毁 -------------------------------------------------------------
    def destroy(self, profile_id: str, confirm_alias: str) -> None:
        record = self._require(profile_id)
        if confirm_alias.strip() != record.alias:
            raise ProfileError("确认别名不匹配，销毁已取消")
        # crypto-shred：先删 wrapped_keys，再删目录（含备份，避免 shred 后仍能从备份恢复）
        self.registry.remove(profile_id)
        shutil.rmtree(self.vaults_dir / profile_id, ignore_errors=True)
        shutil.rmtree(self.backup_dir(profile_id), ignore_errors=True)

    # -- 内部 -------------------------------------------------------------
    def _require(self, profile_id: str) -> ProfileRecord:
        record = self.registry.get(profile_id)
        if record is None:
            raise ProfileError(f"档案不存在：{profile_id}")
        return record

    def _unwrap_role(self, record: ProfileRecord, role: str, unwrap_key: bytes) -> bytes:
        for item in record.wrapped_keys:
            if item.get("role") == role:
                return keys.unwrap_dek(keys.WrappedKey.from_json(item), unwrap_key)
        raise InvalidPassphraseError(f"该档案没有 {role} 密钥包装")

    def _role_kdf_params(self, record: ProfileRecord, role: str) -> KdfParams:
        """取该角色自己的 KDF 参数；早期档案的 owner 只有顶层 ``kdf_params``。"""
        for item in record.wrapped_keys:
            stored = item.get("kdf_params")
            if item.get("role") == role and isinstance(stored, dict):
                return KdfParams.from_json(stored)
        if role == keys.ROLE_OWNER:
            return KdfParams.from_json(record.kdf_params)
        raise ProfileError(f"该档案没有 {role} 角色的派生参数")

    def _open(self, record: ProfileRecord, dek: bytes, role: str) -> UnlockedProfile:
        meta = self.read_meta(record.id, dek)
        return UnlockedProfile(
            profile_id=record.id, alias=record.alias, dek=dek, meta=meta, role=role
        )
