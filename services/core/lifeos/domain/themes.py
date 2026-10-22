"""主题存储（第 11.3 节第 11 步）。

取色与 token 生成全部在浏览器本地完成，图片不上传、不出站；这里只负责把
算好的 tokens_json（亮暗两套）与可选的原图附件落进加密档案，并维护激活状态。

主题只有一套处于激活态：激活某个主题时把其余主题的 is_active 清零，
前端按激活主题覆盖 CSS 变量（第 11.3 节）。
"""

from __future__ import annotations

import json
from typing import Any

from ..db.connection import VaultConnection
from ..util.ids import new_ulid, now_utc_iso
from .errors import InvalidInputError, NotFoundError

# tokens_json 至少要有亮暗两套；映射的键值必须是字符串。
REQUIRED_SCHEMES = ("light", "dark")


def _row(row: Any) -> dict[str, Any]:
    if row is None:
        raise NotFoundError("主题不存在")
    out = dict(row)
    out["tokens"] = json.loads(out.pop("tokens_json"))
    out["is_active"] = bool(out["is_active"])
    return out


def validate_tokens(tokens: Any) -> dict[str, dict[str, str]]:
    """校验 tokens_json 结构：亮暗两套映射，键值都是字符串。

    生成端已经保证对比度达标，这里只做形状校验，不做色彩判断。
    """
    if not isinstance(tokens, dict):
        raise InvalidInputError("主题 token 必须是对象")
    cleaned: dict[str, dict[str, str]] = {}
    for scheme in REQUIRED_SCHEMES:
        block = tokens.get(scheme)
        if not isinstance(block, dict) or not block:
            raise InvalidInputError(f"主题缺少 {scheme} 这一套 token")
        scheme_tokens: dict[str, str] = {}
        for key, value in block.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise InvalidInputError("主题 token 的键值都必须是字符串")
            scheme_tokens[key] = value
        cleaned[scheme] = scheme_tokens
    return cleaned


class ThemeService:
    """theme 表的增删改查与激活。"""

    def __init__(self, conn: VaultConnection) -> None:
        self._conn = conn

    def list(self) -> list[dict[str, Any]]:
        rows = self._conn.query("SELECT * FROM theme ORDER BY created_at DESC")
        return [_row(r) for r in rows]

    def get(self, theme_id: str) -> dict[str, Any]:
        return _row(
            self._conn.query_one("SELECT * FROM theme WHERE id = ?", (theme_id,))
        )

    def active(self) -> dict[str, Any] | None:
        row = self._conn.query_one("SELECT * FROM theme WHERE is_active = 1 LIMIT 1")
        return None if row is None else _row(row)

    def create(
        self,
        name: str,
        tokens: Any,
        *,
        source_attachment_id: str | None = None,
        activate: bool = True,
    ) -> dict[str, Any]:
        if not name or not name.strip():
            raise InvalidInputError("主题需要名字")
        cleaned = validate_tokens(tokens)
        if source_attachment_id is not None:
            exists = self._conn.query_one(
                "SELECT 1 FROM attachment WHERE id = ?", (source_attachment_id,)
            )
            if exists is None:
                raise NotFoundError(f"附件不存在：{source_attachment_id}")

        theme_id = new_ulid()
        with self._conn.transaction() as tx:
            if activate:
                tx.execute("UPDATE theme SET is_active = 0 WHERE is_active = 1")
            tx.execute(
                """
                INSERT INTO theme
                  (id, name, tokens_json, source_attachment_id, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    theme_id,
                    name.strip(),
                    json.dumps(cleaned, ensure_ascii=False),
                    source_attachment_id,
                    1 if activate else 0,
                    now_utc_iso(),
                ),
            )
        return self.get(theme_id)

    def activate(self, theme_id: str) -> dict[str, Any]:
        self.get(theme_id)  # 不存在时抛 404
        with self._conn.transaction() as tx:
            tx.execute("UPDATE theme SET is_active = 0 WHERE is_active = 1")
            tx.execute("UPDATE theme SET is_active = 1 WHERE id = ?", (theme_id,))
        return self.get(theme_id)

    def delete(self, theme_id: str) -> None:
        self.get(theme_id)
        with self._conn.transaction() as tx:
            tx.execute("DELETE FROM theme WHERE id = ?", (theme_id,))
