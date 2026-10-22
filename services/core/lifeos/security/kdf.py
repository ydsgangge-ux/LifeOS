"""Argon2id 密钥派生（第 7.2 节第 1 条）。

默认参数：64 MiB、3 次迭代、4 并行；首次运行时可按设备调到约 500ms。
本模块只做派生，不接触磁盘。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from time import perf_counter

from argon2.low_level import Type, hash_secret_raw

SALT_BYTES = 16
KEY_BYTES = 32

DEFAULT_MEMORY_KIB = 65536  # 64 MiB
DEFAULT_ITERATIONS = 3
DEFAULT_PARALLELISM = 4


@dataclass(frozen=True)
class KdfParams:
    """Argon2id 参数，序列化后写入 registry.db 的 kdf_params。"""

    m: int = DEFAULT_MEMORY_KIB
    t: int = DEFAULT_ITERATIONS
    p: int = DEFAULT_PARALLELISM
    salt: str = ""  # 十六进制

    def to_json(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, object]) -> KdfParams:
        # 值来自 registry.db 的 JSON，统一经 str 再转 int，兼容 int 与字符串两种写法
        return cls(
            m=int(str(data["m"])),
            t=int(str(data["t"])),
            p=int(str(data["p"])),
            salt=str(data["salt"]),
        )

    def salt_bytes(self) -> bytes:
        return bytes.fromhex(self.salt)


def new_salt() -> str:
    return os.urandom(SALT_BYTES).hex()


def derive_kek(passphrase: str, params: KdfParams) -> bytes:
    """由口令派生 32 字节 KEK。"""
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=params.salt_bytes(),
        time_cost=params.t,
        memory_cost=params.m,
        parallelism=params.p,
        hash_len=KEY_BYTES,
        type=Type.ID,
    )


def calibrate(target_ms: int = 500, salt: str | None = None) -> KdfParams:
    """把参数调到约 target_ms（第 7.2 节第 1 条）。

    固定 t、p，二分 memory_kib 直到耗时接近目标。
    """
    salt = salt or new_salt()
    lo, hi = 8192, 1 << 21
    best = KdfParams(m=lo, salt=salt)
    probe = "calibration-probe"
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = KdfParams(m=mid, salt=salt)
        t0 = perf_counter()
        derive_kek(probe, cand)
        elapsed_ms = (perf_counter() - t0) * 1000
        best = cand
        if elapsed_ms < target_ms * 0.85:
            lo = mid * 2
        elif elapsed_ms > target_ms * 1.15:
            hi = mid // 2
        else:
            break
    return best
