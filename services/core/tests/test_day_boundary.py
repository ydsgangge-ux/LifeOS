"""逻辑日（凌晨 0–4 点归前一天）：日期折算、周期边界、录入落库与历史迁移。

依据：用户口径「04:00 归前一天」——用户常在 0 点后才有空整理刚过完的一天，
按午夜切日会让昨天看起来没记录，也会让凌晨生成的月报/年报跑到新周期。
"""

from __future__ import annotations

import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from lifeos.analytics import periods as P
from lifeos.db.connection import open_vault
from lifeos.db.migrator import MIGRATIONS_DIR, migrate
from lifeos.util.ids import logical_date, occurred_date_for

CST = timezone(timedelta(hours=8))


def _fresh(tmp_path: Path):
    path = tmp_path / "vault.db"
    return open_vault(path, os.urandom(32)), path


def test_logical_date_before_cutoff_goes_to_previous_day() -> None:
    assert logical_date(datetime(2026, 10, 25, 3, 59, tzinfo=CST)) == date(2026, 10, 24)


def test_logical_date_at_cutoff_stays_same_day() -> None:
    assert logical_date(datetime(2026, 10, 25, 4, 0, tzinfo=CST)) == date(2026, 10, 25)


def test_occurred_date_for_uses_timestamp_own_offset() -> None:
    # 记录自带偏移：手机端提交的时间也按它自己的时区折算
    assert occurred_date_for("2026-10-25T00:30:00+08:00") == "2026-10-24"
    assert occurred_date_for("2026-10-25T03:59:59+08:00") == "2026-10-24"
    assert occurred_date_for("2026-10-25T04:00:00+08:00") == "2026-10-25"
    # 只有日期视为用户显式指定，不再折算；解析失败也退回前 10 位
    assert occurred_date_for("2026-10-25") == "2026-10-25"
    assert occurred_date_for("2026-10-25T99:99") == "2026-10-25"


def test_periods_follow_logical_today(monkeypatch: pytest.MonkeyPatch) -> None:
    # 10 月 31 日 00:30 生成时，逻辑日仍是 10 月 31 日：月报/年报不会跑到新周期
    monkeypatch.setattr(P, "logical_today", lambda: date(2026, 10, 31))
    assert P.resolve_period("monthly") == (date(2026, 10, 1), date(2026, 10, 31))
    assert P.resolve_period("yearly") == (date(2026, 1, 1), date(2026, 12, 31))
    assert P.resolve_period("weekly") == (date(2026, 10, 25), date(2026, 10, 31))


def test_entry_created_after_midnight_counts_to_previous_day(fast_vault, monkeypatch) -> None:
    from lifeos.domain import entries as entries_mod

    monkeypatch.setattr(entries_mod, "_local_now", lambda: "2026-10-25T01:30:00+08:00")
    entry = fast_vault.service.entries.create({"domain": "daily", "note": "凌晨补记"})
    assert entry["occurred_date"] == "2026-10-24"
    # 真实时刻不动：正文里仍能看到这是一条凌晨记录
    assert str(entry["occurred_at"]).startswith("2026-10-25T01:30")


def test_entry_created_at_cutoff_stays_same_day(fast_vault, monkeypatch) -> None:
    from lifeos.domain import entries as entries_mod

    monkeypatch.setattr(entries_mod, "_local_now", lambda: "2026-10-25T04:00:00+08:00")
    entry = fast_vault.service.entries.create({"domain": "daily", "note": "天亮后"})
    assert entry["occurred_date"] == "2026-10-25"


def test_migration_shifts_only_legacy_midnight_rows(tmp_path: Path) -> None:
    """0012 把 0–4 点的历史记录退回前一天，白天记录与纯日期记录保持不变。"""
    mig_dir = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, mig_dir)
    cutoff_file = mig_dir / "0012_logical_day_cutoff.sql"
    cutoff_file.unlink()

    conn, path = _fresh(tmp_path)
    migrate(conn, path, mig_dir)
    conn.execute(
        "INSERT INTO entry (id, domain, occurred_at, occurred_date, title, created_at, updated_at)"
        " VALUES ('A','daily','2026-10-25T01:00:00+08:00','2026-10-25','凌晨',"
        "'2026-10-25T01:00:00+00:00','2026-10-25T01:00:00+00:00'),"
        "        ('B','daily','2026-10-25T12:00:00+08:00','2026-10-25','白天',"
        "'2026-10-25T12:00:00+00:00','2026-10-25T12:00:00+00:00'),"
        "        ('C','daily','2026-10-25','2026-10-25','只有日期',"
        "'2026-10-25T12:00:00+00:00','2026-10-25T12:00:00+00:00')"
    )

    shutil.copy2(MIGRATIONS_DIR / "0012_logical_day_cutoff.sql", cutoff_file)
    assert migrate(conn, path, mig_dir) == [12]

    rows = {
        str(row["id"]): str(row["occurred_date"])
        for row in conn.query("SELECT id, occurred_date FROM entry")
    }
    # C 只有日期：视为用户显式指定，不能被折算
    assert rows == {"A": "2026-10-24", "B": "2026-10-25", "C": "2026-10-25"}
    conn.close()
