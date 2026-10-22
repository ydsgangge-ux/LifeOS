"""附件加密存储（FR-05，第 7.2 节第 5 条）。

每个文件用 DEK 经 HKDF 派生子密钥，AES-256-GCM 加密后写入 blobs/；
数据库只存元数据与明文 sha256。缩略图同样加密。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..db.connection import VaultConnection
from ..security import blobs as blob_crypto
from ..util.ids import new_ulid, now_utc_iso
from .errors import InvalidInputError, NotFoundError

ATTACHMENT_KINDS = ("image", "audio", "doc")


class AttachmentService:
    """附件的加解密存取。"""

    def __init__(self, conn: VaultConnection, dek: bytes, blobs_dir: str | Path) -> None:
        self._conn = conn
        self._dek = dek
        self._blobs = Path(blobs_dir)
        self._blobs.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        data: bytes,
        kind: str = "image",
        mime: str | None = None,
        entry_id: str | None = None,
    ) -> dict[str, Any]:
        if kind not in ATTACHMENT_KINDS:
            raise InvalidInputError(f"未知附件类型：{kind}")
        if entry_id is not None:
            exists = self._conn.query_one("SELECT 1 FROM entry WHERE id = ?", (entry_id,))
            if exists is None:
                raise NotFoundError(f"条目不存在：{entry_id}")

        attachment_id = new_ulid()
        blob_name = f"{attachment_id}.bin"
        digest = hashlib.sha256(data).hexdigest()
        (self._blobs / blob_name).write_bytes(blob_crypto.encrypt_blob(data, self._dek))
        self._conn.execute(
            """
            INSERT INTO attachment
              (id, entry_id, kind, blob_name, mime, size_bytes, sha256, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (attachment_id, entry_id, kind, blob_name, mime, len(data), digest, now_utc_iso()),
        )
        return self.get(attachment_id)

    def get(self, attachment_id: str) -> dict[str, Any]:
        row = self._conn.query_one("SELECT * FROM attachment WHERE id = ?", (attachment_id,))
        if row is None:
            raise NotFoundError(f"附件不存在：{attachment_id}")
        return dict(row)

    def read(self, attachment_id: str) -> tuple[bytes, dict[str, Any]]:
        meta = self.get(attachment_id)
        raw = (self._blobs / meta["blob_name"]).read_bytes()
        return blob_crypto.decrypt_blob(raw, self._dek), meta

    def list_for_entry(self, entry_id: str) -> list[dict[str, Any]]:
        rows = self._conn.query(
            "SELECT * FROM attachment WHERE entry_id = ? ORDER BY created_at", (entry_id,)
        )
        return [dict(r) for r in rows]

    def delete(self, attachment_id: str) -> None:
        meta = self.get(attachment_id)
        with self._conn.transaction() as tx:
            tx.execute("DELETE FROM attachment WHERE id = ?", (attachment_id,))
        (self._blobs / meta["blob_name"]).unlink(missing_ok=True)
