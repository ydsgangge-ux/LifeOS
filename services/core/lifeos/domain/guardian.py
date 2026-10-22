"""监护人可见范围策略（第 7.7 节）。

**重要局限（第 7.7 节）**：监护人持有 DEK 时技术上能读取全部数据，本模块实现的
只是**应用层**可见范围，不是密码学隔离。界面必须如实说明这一点（验收 A3）。

把「guardian 会话下哪些内容该被隐藏」的判定集中在这里一处，条目、报告、图谱、
事实包四处读路径共用同一个函数，避免各写一份导致口径漂移（验收 A2）。
默认值严格照文档 7.7 L1041：可见 daily 汇总、plan、education、hobby；
不可见 dream、relation 细节与日记文本，需孩子逐条分享。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..security.keys import ROLE_GUARDIAN, ROLE_OWNER

# 默认可见的领域（文档 7.7 L1041）
DEFAULT_VISIBLE_DOMAINS: tuple[str, ...] = ("daily", "plan", "education", "hobby")
# 细节默认不可见的领域：relation 的冲突/细节、dream 原文
DETAIL_GATED_DOMAINS: tuple[str, ...] = ("dream", "relation")

ENTRY_VISIBLE = "guardian_visible"


@dataclass(frozen=True)
class GuardianPolicy:
    """guardian_policy_json 的结构（文档 7.7）。"""

    visible_domains: tuple[str, ...] = DEFAULT_VISIBLE_DOMAINS
    # 日记文本（daily 的 note）默认不可见，需孩子逐条分享
    allow_journal_text: bool = False
    # relation / dream 的细节默认不可见
    allow_detail: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "visible_domains": list(self.visible_domains),
            "allow_journal_text": self.allow_journal_text,
            "allow_detail": self.allow_detail,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> GuardianPolicy:
        if not raw:
            return cls()
        domains = raw.get("visible_domains")
        visible: tuple[str, ...] = (
            tuple(str(d) for d in domains)
            if isinstance(domains, list)
            else DEFAULT_VISIBLE_DOMAINS
        )
        return cls(
            visible_domains=visible,
            allow_journal_text=bool(raw.get("allow_journal_text", False)),
            allow_detail=bool(raw.get("allow_detail", False)),
        )

    def can_see_domain(self, domain: str) -> bool:
        return domain in self.visible_domains

    def can_see_entry(self, *, domain: str, visibility: str) -> bool:
        """单条记录在 guardian 会话下是否可见（验收 A2 的单一判定入口）。

        - 白名单领域默认可见；
        - 其余领域（含 dream、relation）需孩子逐条把 visibility 改成 guardian_visible；
        - relation / dream 的细节还要 policy.allow_detail 才展开。
        """
        if domain in self.visible_domains:
            return True
        if domain in DETAIL_GATED_DOMAINS and not self.allow_detail:
            return False
        return visibility == ENTRY_VISIBLE

    def may_read_note(self, domain: str) -> bool:
        """note（日记正文）是否可见：daily 的正文默认藏起来，需逐条分享。"""
        if domain == "daily":
            return self.allow_journal_text
        return True


DEFAULT_POLICY = GuardianPolicy()


def default_policy_json() -> dict[str, Any]:
    return DEFAULT_POLICY.to_json()


@dataclass(frozen=True)
class Viewer:
    """「谁在看这个档案」：owner 看全部；guardian 按策略裁剪（第 7.7 节、验收 A2）。

    条目、报告、图谱、事实包四处读路径共用同一个 Viewer，避免各写一份判定。
    """

    role: str = ROLE_OWNER
    policy: GuardianPolicy = DEFAULT_POLICY

    @property
    def is_guardian(self) -> bool:
        return self.role == ROLE_GUARDIAN

    @property
    def visible_domains(self) -> frozenset[str] | None:
        """事实包/指标按领域裁剪时的白名单；owner 返回 None（不裁剪）。"""
        return frozenset(self.policy.visible_domains) if self.is_guardian else None


OWNER_VIEWER = Viewer()


def viewer_for(role: str, policy: dict[str, Any] | None = None) -> Viewer:
    """按解锁角色与档案里的 guardian_policy 构造 Viewer。"""
    if role != ROLE_GUARDIAN:
        return OWNER_VIEWER
    return Viewer(role=role, policy=GuardianPolicy.from_json(policy))
