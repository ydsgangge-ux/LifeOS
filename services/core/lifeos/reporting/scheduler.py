"""报告调度与补跑（第 3.5 节 B 第 1 步、第 9.1 节）。

- 周报：每周日 21:00（config [reports] weekly_cron）。
- 月报：每月最后一天。
- 错过就在下次启动时补跑——进程内 APScheduler，不做系统计划任务。

「该跑哪个周期」与「这个周期跑过没有」是纯逻辑，单独拆出来便于测试：
due_periods 只按时间算，missing_periods 再查 report 表去重，保证同一周期只生成一次。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..analytics.service import AnalyticsService
from .pipeline import generate_report
from .store import report_exists

WEEKLY_RUN_HOUR = 21
_MONTHLY_RUN_HOUR = 21
_YEARLY_RUN_HOUR = 21


def weekly_due_end(now: datetime) -> date:
    """周报该覆盖到的那一天：最近一个已过 21:00 的周日。"""
    days_since_sunday = (now.weekday() - 6) % 7
    candidate = now.date() - timedelta(days=days_since_sunday)
    if candidate == now.date() and now.hour < WEEKLY_RUN_HOUR:
        candidate -= timedelta(days=7)
    return candidate


def _last_day_of_month(day: date) -> date:
    first = day.replace(day=1)
    return (first + timedelta(days=31)).replace(day=1) - timedelta(days=1)


def monthly_due_end(now: datetime) -> date:
    """月报该覆盖的最后一个自然日：本月最后一天（若已过 21:00），否则上月末。"""
    last_this = _last_day_of_month(now.date())
    if now.date() == last_this and now.hour >= _MONTHLY_RUN_HOUR:
        return last_this
    return now.date().replace(day=1) - timedelta(days=1)


def yearly_due_end(now: datetime) -> date | None:
    """年报只在自然年结束后到点：12/31 21:00 后，或次年 1 月补跑。

    其余月份不排年报——否则每次启动都会给「去年」补一份几乎空白的年报。
    """
    if now.month == 12 and now.day == 31 and now.hour >= _YEARLY_RUN_HOUR:
        return now.date()
    if now.month == 1:
        return date(now.year - 1, 12, 31)
    return None


def due_periods(now: datetime) -> list[dict[str, str]]:
    periods = [
        {"kind": "weekly", "end": weekly_due_end(now).isoformat()},
        {"kind": "monthly", "end": monthly_due_end(now).isoformat()},
    ]
    yearly_end = yearly_due_end(now)
    if yearly_end is not None:
        periods.append({"kind": "yearly", "end": yearly_end.isoformat()})
    return periods


def missing_periods(conn: Any, now: datetime) -> list[dict[str, str]]:
    """到点但还没生成过的周期（补跑清单）。"""
    return [
        item
        for item in due_periods(now)
        if not report_exists(conn, item["kind"], item["end"])
    ]


class ReportScheduler:
    """进程内调度器；持有已解锁的分析服务，补跑与定时都走同一条流水线。"""

    def __init__(
        self,
        analytics: AnalyticsService,
        *,
        now: Callable[[], datetime] = datetime.now,
        transport: Any = None,
    ) -> None:
        self.analytics = analytics
        self.config = analytics.config
        self._now = now
        self._transport = transport
        self._scheduler: BackgroundScheduler | None = None

    def run_due(self) -> list[dict[str, Any]]:
        """补跑所有到点未生成的周期，返回生成的报告。"""
        reports: list[dict[str, Any]] = []
        for item in missing_periods(self.analytics.conn, self._now()):
            reports.append(
                generate_report(
                    self.analytics,
                    kind=item["kind"],
                    end=item["end"],
                    transport=self._transport,
                )
            )
        return reports

    def start(self) -> None:
        """启动定时任务，并立即做一次补跑（错过的那几次在这里补上）。"""
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            self.run_due,
            CronTrigger.from_crontab(self.config.weekly_cron),
            id="weekly_report",
            replace_existing=True,
        )
        scheduler.add_job(
            self.run_due,
            CronTrigger(day="last", hour=_MONTHLY_RUN_HOUR, minute=0),
            id="monthly_report",
            replace_existing=True,
        )
        scheduler.add_job(
            self.run_due,
            CronTrigger(month="12", day="last", hour=_YEARLY_RUN_HOUR, minute=0),
            id="yearly_report",
            replace_existing=True,
        )
        scheduler.start()
        self._scheduler = scheduler
        self.run_due()

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
