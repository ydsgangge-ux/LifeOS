"""开发种子数据（第 13.5 节）。

生成多档案、跨 3 年的假数据，覆盖各年龄档，并包含意外与关系边，用于
演示、性能测试和泄漏黄金测试。

**开发与测试一律不得使用真实个人数据**——这里全部是随机编造的假数据。

用法（在仓库根目录）::

    .venv\\Scripts\\python.exe scripts/seed.py --data-dir .tmp/demo

默认数据目录是 ``.tmp/demo``，不会碰你真实使用的 ``~/.lifeos``。
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "services" / "core") not in sys.path:
    sys.path.insert(0, str(_ROOT / "services" / "core"))

from lifeos.config import load_config  # noqa: E402
from lifeos.domain.profiles import ProfileService  # noqa: E402
from lifeos.domain.service import DomainService  # noqa: E402
from lifeos.util.ids import local_iso, now_utc_iso  # noqa: E402

DEFAULT_PASSPHRASE = "lifeos-demo"

# 每个年龄档一个演示档案（第 2.1 节）
PROFILE_PLAN: tuple[tuple[str, str, int], ...] = (
    ("演示档案·成人", "adult", 1990),
    ("演示档案·青少年", "teen", 2010),
    ("演示档案·儿童", "child", 2017),
)

SURNAMES = ("王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴")
GIVEN = ("小明", "阿强", "小雨", "阿哲", "婷婷", "阿毛", "小舟", "阿阳")
EXPENSE_CATEGORIES = ("餐饮", "交通", "住房", "娱乐", "学习", "医疗", "人情", "其它")
INCOME_SOURCES = ("工资", "奖金", "副业", "红包", "利息")
EXERCISE_TYPES = ("跑步", "游泳", "骑行", "力量训练", "散步", "球类")
MOOD_LINES = (
    "还行，按计划过完了",
    "有点累但值得",
    "状态一般，早点睡",
    "今天很专注",
    "心情不错，见了朋友",
)


def _dt(day: date, hour: int, minute: int = 0) -> str:
    """本地时区带偏移的 ISO 时间（第 4.1 节 occurred_at 约定）。"""
    return local_iso(datetime.combine(day, time(hour, minute)).astimezone())


def _person_name(rng: random.Random) -> str:
    return rng.choice(SURNAMES) + rng.choice(GIVEN)


def _seed_people(service: DomainService, rng: random.Random, count: int) -> list[dict[str, Any]]:
    people = []
    for _ in range(count):
        people.append(
            service.longterm.create_person(
                {
                    "display_name": _person_name(rng),
                    "relation_type": rng.choice(("family", "friend", "colleague", "mentor")),
                    "closeness": rng.randint(1, 5),
                }
            )
        )
    return people


def _seed_longterm(service: DomainService) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    goal = service.longterm.create_goal(
        {
            "title": "一年读 12 本书",
            "area": "education",
            "horizon": "1y",
            "metric_name": "读完的书本数",
            "metric_unit": "本",
            "direction": "increase",
            "baseline": 0,
            "target": 12,
            "why_note": "想找回长时间阅读的专注感",
        }
    )
    plans = [
        service.longterm.create_plan(
            {
                "goal_id": goal["id"],
                "title": "每天早上读 30 分钟",
                "cadence": "daily",
                "start_date": "2024-01-01",
                "expected_output": "每月一本",
                "output_metric": "分钟",
                "est_minutes_per_week": 210,
            }
        ),
        service.longterm.create_plan(
            {
                "goal_id": goal["id"],
                "title": "每周写一篇读书笔记",
                "cadence": "weekly",
                "start_date": "2025-03-01",
                "expected_output": "4 篇/月",
            }
        ),
    ]
    return goal, plans


def _seed_days(
    service: DomainService,
    rng: random.Random,
    *,
    start: date,
    end: date,
    goal: dict[str, Any],
    plans: list[dict[str, Any]],
    people: list[dict[str, Any]],
) -> int:
    """按天铺数据：日常、开销、健康、计划打卡，附带意外与关系边。"""
    written = 0
    day = start
    while day <= end:
        if rng.random() < 0.6:
            service.entries.create(
                {
                    "domain": "daily",
                    "occurred_at": _dt(day, 21, 30),
                    "title": "今日小结",
                    "detail": {
                        "mood": rng.randint(1, 5),
                        "energy": rng.randint(1, 5),
                        "stress": rng.randint(1, 5),
                        "day_rating": rng.randint(1, 5),
                        "one_line": rng.choice(MOOD_LINES),
                    },
                }
            )
            written += 1

        if rng.random() < 0.4:
            service.entries.create(
                {
                    "domain": "health",
                    "occurred_at": _dt(day, 7, 30),
                    "title": "作息与运动",
                    "detail": {
                        "sleep_hours": round(rng.uniform(4.5, 9.5), 1),
                        "sleep_quality": rng.randint(1, 5),
                        "exercise_minutes": rng.choice((0, 15, 30, 45, 60, 90)),
                        "exercise_type": rng.choice(EXERCISE_TYPES),
                        "diet_quality": rng.randint(1, 5),
                    },
                }
            )
            written += 1

        for _ in range(rng.randint(0, 2)):
            entry = service.entries.create(
                {
                    "domain": "expense",
                    "occurred_at": _dt(day, rng.randint(7, 22), rng.randint(0, 59)),
                    "title": rng.choice(EXPENSE_CATEGORIES),
                    "detail": {
                        "amount_minor": rng.randint(800, 60000),
                        "currency": "CNY",
                        "minor_exp": 2,
                        "category": rng.choice(EXPENSE_CATEGORIES),
                        "essential": rng.choice((0, 1)),
                        "recurring": 0,
                    },
                }
            )
            written += 1

            # 约 6% 的开销带「意外」标注（第 5.2 节）
            if rng.random() < 0.06:
                service.entries.add_anomaly(
                    {
                        "type": rng.choice(("finance", "health", "relationship", "other")),
                        "impact": rng.randint(1, 5),
                        "cause_note": "临时情况，记录一下",
                    },
                    entry["id"],
                )

            # 约 8% 的开销挂到某个人身上（关系边由触发器校验目标存在）
            if rng.random() < 0.08:
                service.entries.create_link(
                    "entry",
                    entry["id"],
                    {
                        "dst_type": "person",
                        "dst_id": rng.choice(people)["id"],
                        "relation": "involves_person",
                        "created_by": "user",
                    },
                )

        if day.day in (10, 25):  # 发薪日
            service.entries.create(
                {
                    "domain": "income",
                    "occurred_at": _dt(day, 9, 0),
                    "title": rng.choice(INCOME_SOURCES),
                    "detail": {
                        "amount_minor": rng.randint(500000, 3000000),
                        "currency": "CNY",
                        "minor_exp": 2,
                        "category": rng.choice(INCOME_SOURCES),
                    },
                }
            )
            written += 1

        # 工作日大概率执行了「早上读书」
        if day.weekday() < 5 and rng.random() < 0.7:
            service.entries.create(
                {
                    "domain": "plan",
                    "occurred_at": _dt(day, 7, 0),
                    "title": "计划执行",
                    "detail": {
                        "plan_id": plans[0]["id"],
                        "planned_units": 30,
                        "actual_units": rng.choice((0, 15, 30, 35, 45)),
                        "unit": "分钟",
                        "outcome": rng.choice(("done", "partial", "skipped")),
                    },
                }
            )
            written += 1

        day += timedelta(days=1)

    # 目标回看：每月一条（形态与 POST /goals/{id}/checkpoints 一致）
    check = start.replace(day=1)
    while check <= end:
        service.entries.create(
            {
                "domain": "goal",
                "occurred_at": _dt(check, 20, 0),
                "title": "目标回看",
                "detail": {
                    "goal_id": goal["id"],
                    "metric_value": round(rng.uniform(0, 12), 1),
                    "confidence": rng.randint(1, 5),
                    "decision": rng.choice(("keep", "keep", "revise")),
                    "progress_note": "月度回看",
                },
            }
        )
        written += 1
        check = (check + timedelta(days=31)).replace(day=1)
    return written


def seed(
    data_dir: Path,
    *,
    profiles: int = 3,
    years: int = 3,
    rng_seed: int = 20261015,
    passphrase: str = DEFAULT_PASSPHRASE,
) -> list[dict[str, Any]]:
    """建档案并铺数据，返回每个档案的摘要。"""
    config = load_config(data_dir)
    profiles_service = ProfileService(config.data_dir)
    today = date.today()
    start = today - timedelta(days=365 * years)
    summary: list[dict[str, Any]] = []

    for alias, band, birth_year in PROFILE_PLAN[:profiles]:
        result = profiles_service.create(
            alias, passphrase, age_band=band, birth_year=birth_year
        )
        unlocked = profiles_service.unlock(result.profile_id, passphrase)
        conn_path = profiles_service.vault_path(result.profile_id)
        from lifeos.db.connection import open_vault

        conn = open_vault(conn_path, unlocked.dek)
        try:
            service = DomainService(
                conn, unlocked.dek, profiles_service.blobs_dir(result.profile_id)
            )
            rng = random.Random(f"{rng_seed}-{alias}")
            people = _seed_people(service, rng, 4 if band != "child" else 2)
            goal, plans = _seed_longterm(service)
            written = _seed_days(
                service,
                rng,
                start=start,
                end=today,
                goal=goal,
                plans=plans,
                people=people,
            )
            service.longterm.create_hobby("徒步")
            service.longterm.create_hobby("摄影")
            skill = service.longterm.create_skill("吉他", "hobby")
            service.longterm.add_skill_level(skill["id"], 2.5, today.isoformat())
        finally:
            conn.close()

        summary.append(
            {
                "profile_id": result.profile_id,
                "alias": alias,
                "age_band": band,
                "entries": written,
                "people": len(people),
                "recovery_key": result.recovery_key,
            }
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 LifeOS 开发演示数据")
    parser.add_argument(
        "--data-dir",
        default=str(_ROOT / ".tmp" / "demo"),
        help="数据目录（默认 .tmp/demo，刻意避开 ~/.lifeos）",
    )
    parser.add_argument("--profiles", type=int, default=len(PROFILE_PLAN))
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261015, help="随机种子，保证可复现")
    parser.add_argument("--passphrase", default=DEFAULT_PASSPHRASE)
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).expanduser().resolve()
    print(f"数据目录：{data_dir}")
    print(f"口令：{args.passphrase}（演示用，请勿用于真实数据）")
    summary = seed(
        data_dir,
        profiles=args.profiles,
        years=args.years,
        rng_seed=args.seed,
        passphrase=args.passphrase,
    )
    print(f"完成时间：{now_utc_iso()}")
    for item in summary:
        print(
            f"  {item['alias']}（{item['age_band']}）：{item['entries']} 条记录，"
            f"{item['people']} 个人物"
        )
        print(f"    恢复密钥：{item['recovery_key']}")
    print("提示：演示数据一律为编造内容，不得混入真实个人数据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
