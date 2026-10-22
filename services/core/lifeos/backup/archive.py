"""加密备份归档的读写（第 7.6 节）。

一个备份文件（.lifeosbak）由三段组成：

    magic(4) | version(1) | salt(16) | nonce(12) | meta_len(4) | meta(JSON) | 密文 | tag(16)

- **外层加密**：载荷用 DEK 经 HKDF-SHA256 派生的子密钥 AES-256-GCM 加密。
  vault.db 与附件本身已各自被 DEK 加密，这里再包一层，是为了让「一整份备份」
  可以用一把密钥整体校验完整性：任何一页被改动，GCM 的 tag 都会对不上。
- **明文头**：只放恢复所必需、且不含业务数据的信息（档案 id/别名、KDF 参数、
  被包装的密钥、库指纹）。被包装的密钥离开口令/恢复密钥也没有用，故可明文。
- **AAD**：头部的全部字节都参与认证，改一个字节都打不开。

流式加解密，避免把整库读进内存（备份可能很大）。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..security.errors import SecurityError

MAGIC = b"LSBK"
FORMAT_VERSION = 1
SALT_BYTES = 16
NONCE_BYTES = 12
TAG_BYTES = 16
HKDF_INFO = b"lifeos-backup-v1"
_CHUNK = 1 << 20

# 头部固定前缀：magic(4) + version(1) + salt(16) + nonce(12) + meta_len(4)
_PREFIX_BYTES = 4 + 1 + SALT_BYTES + NONCE_BYTES + 4

BACKUP_SUFFIX = ".lifeosbak"


class BackupError(SecurityError):
    """备份文件格式错误、内容被篡改或恢复条件不成立。"""


@dataclass(frozen=True)
class ArchiveHeader:
    meta: dict[str, Any]
    salt: bytes
    nonce: bytes
    data_offset: int  # 密文起点
    tag_offset: int  # tag 起点（= 文件大小 - 16）


def _derive_backup_key(dek: bytes, salt: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=HKDF_INFO,
    ).derive(dek)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_header(path: str | Path) -> ArchiveHeader:
    """只读头部，用来在解密前拿到档案元数据与派生参数。"""
    p = Path(path)
    if not p.exists():
        raise BackupError(f"备份文件不存在：{p}")
    total = p.stat().st_size
    with p.open("rb") as fh:
        prefix = fh.read(_PREFIX_BYTES)
        if len(prefix) < _PREFIX_BYTES or prefix[:4] != MAGIC:
            raise BackupError("备份文件格式不正确或已损坏")
        version = prefix[4]
        if version != FORMAT_VERSION:
            raise BackupError(f"不支持的备份格式版本：{version}")
        salt = prefix[5 : 5 + SALT_BYTES]
        nonce = prefix[5 + SALT_BYTES : 5 + SALT_BYTES + NONCE_BYTES]
        (meta_len,) = struct.unpack(">I", prefix[-4:])
        raw_meta = fh.read(meta_len)
        if len(raw_meta) < meta_len:
            raise BackupError("备份文件头部不完整")
    try:
        meta = json.loads(raw_meta)
    except ValueError as exc:
        raise BackupError("备份文件头部不是合法 JSON") from exc
    if not isinstance(meta, dict):
        raise BackupError("备份文件头部结构不合法")
    data_offset = _PREFIX_BYTES + meta_len
    if total < data_offset + TAG_BYTES:
        raise BackupError("备份文件内容不完整")
    return ArchiveHeader(
        meta=meta,
        salt=salt,
        nonce=nonce,
        data_offset=data_offset,
        tag_offset=total - TAG_BYTES,
    )


def _aad(header_bytes: bytes) -> bytes:
    return header_bytes


def write_archive(
    dest: str | Path,
    meta: dict[str, Any],
    payload_dir: Path,
    dek: bytes,
) -> Path:
    """把 payload_dir 下的文件（vault.db、blobs/…）打包并加密写入 dest。"""
    p = Path(dest)
    p.parent.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    key = _derive_backup_key(dek, salt)

    # 先打成 tar（未压缩，避免额外内存/CPU），再流式加密
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for file in sorted(payload_dir.rglob("*")):
            if file.is_file():
                tar.add(file, arcname=str(file.relative_to(payload_dir)))
    payload = buffer.getvalue()

    meta_bytes = json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8")
    prefix = MAGIC + bytes([FORMAT_VERSION]) + salt + nonce + struct.pack(">I", len(meta_bytes))
    header_bytes = prefix + meta_bytes

    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(_aad(header_bytes))
    with p.open("wb") as out:
        out.write(header_bytes)
        for offset in range(0, len(payload), _CHUNK):
            out.write(encryptor.update(payload[offset : offset + _CHUNK]))
        out.write(encryptor.finalize())
        out.write(encryptor.tag)
    return p


def extract_archive(path: str | Path, dest_dir: Path, dek: bytes) -> ArchiveHeader:
    """校验并解密归档，把内容解到 dest_dir。返回头部（含 meta）。"""
    header = read_header(path)
    key = _derive_backup_key(dek, header.salt)

    p = Path(path)
    with p.open("rb") as fh:
        header_bytes = fh.read(header.data_offset)
        fh.seek(header.tag_offset)
        tag = fh.read(TAG_BYTES)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(header.nonce)).decryptor()
        decryptor.authenticate_additional_data(_aad(header_bytes))
        remaining = header.tag_offset - header.data_offset
        tar_buffer = io.BytesIO()
        try:
            fh.seek(header.data_offset)
            while remaining > 0:
                chunk = fh.read(min(_CHUNK, remaining))
                remaining -= len(chunk)
                tar_buffer.write(decryptor.update(chunk))
            decryptor.finalize_with_tag(tag)
        except InvalidTag as exc:
            raise BackupError("备份解密失败：文件已损坏或被篡改") from exc

    dest_dir.mkdir(parents=True, exist_ok=True)
    tar_buffer.seek(0)
    with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
        for member in tar.getmembers():
            _assert_safe_member(member.name)
        # filter="data" 会在解包时再拒一次绝对路径/逃逸成员（Py3.12+）
        tar.extractall(dest_dir, filter="data")
    return header


def _assert_safe_member(name: str) -> None:
    """拒绝绝对路径与 .. 逃逸，避免备份文件被构造来覆盖归档外的文件。"""
    parts = Path(name).parts
    if name.startswith("/") or ".." in parts:
        raise BackupError(f"备份包含非法路径：{name}")


def move_into(src: Path, dest: Path) -> None:
    """把恢复出来的内容搬到目标目录（目标已提前腾空）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
