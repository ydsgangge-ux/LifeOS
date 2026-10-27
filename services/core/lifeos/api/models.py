"""API 请求体模型（第 12 章）。

字段可选的原则见第 6.1 节：表单里所有字段都是可选的，校验只在落库时
按非空记录规则（第 4.1 节）判断。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AgeBand = Literal["child", "teen", "adult", "senior"]


# ---------------------------------------------------------------------------
# 档案
# ---------------------------------------------------------------------------
class ProfileCreateRequest(BaseModel):
    alias: str
    passphrase: str
    age_band: AgeBand | None = None
    birth_year: int | None = None
    region_level: str | None = None
    base_currency: str = "CNY"
    # 第 7.7 节：未满 14 周岁创建档案需监护人确认同意；可顺带设置监护人口令
    guardian_consent: bool = False
    guardian_passphrase: str | None = None


class UnlockRequest(BaseModel):
    """口令或恢复密钥二选一（第 7.2 节第 4 条）。

    ``role='guardian'`` 时用监护人口令解锁（第 7.7 节）；缺省按本人口令解锁。
    """

    passphrase: str | None = None
    recovery_key: str | None = None
    role: Literal["owner", "guardian"] | None = None


class GuardianPassphrase(BaseModel):
    """POST /profiles/{id}/guardian：挂一枚监护人密钥包装。"""

    passphrase: str


class GuardianPolicyUpdate(BaseModel):
    """PUT /profiles/{id}/guardian-policy：调整监护人可见范围（第 7.7 节）。"""

    model_config = ConfigDict(extra="ignore")

    visible_domains: list[str] | None = None
    allow_journal_text: bool | None = None
    allow_detail: bool | None = None


class ChangePassphraseRequest(BaseModel):
    old: str
    new: str


class LocaleUpdate(BaseModel):
    """PUT /profiles/{id}/locale：把界面语言记进档案（NFR-08、P6 W4）。

    语言码形如 ``zh-CN`` / ``en``，须在可用词表之内；列已存在（0001_init），不新增迁移。
    """

    locale: str


class DestroyRequest(BaseModel):
    """销毁需要二次确认，并输入档案别名（第 7.6 节）。"""

    confirm_alias: str


# ---------------------------------------------------------------------------
# 条目
# ---------------------------------------------------------------------------
class EntrySubmit(BaseModel):
    """POST /entries 的请求体，支持两种形态。

    - 表单提交：``form_id`` + ``values``，由后端用同一份 schema 校验（第 3.5 节 A）。
    - 结构化提交：直接给 ``domain``、``title``、``detail`` 等字段。

    顶层未声明的键一律收下，由领域层归入 ``extra_json``（第 6.4 节）。
    """

    model_config = ConfigDict(extra="allow")

    form_id: str | None = None
    values: dict[str, Any] | None = None


class EntryPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    note: str | None = None
    occurred_at: str | None = None
    status: Literal["draft", "final"] | None = None
    visibility: Literal["private", "guardian_visible"] | None = None
    sensitivity: int | None = Field(default=None, ge=0, le=3)
    domain: str | None = None
    tags: list[str] | None = None
    extra: dict[str, Any] | None = None
    detail: dict[str, Any] | None = None


class AnomalyCreate(BaseModel):
    type: str
    impact: int = Field(ge=1, le=5)
    one_off: bool = True
    exclusion_mode: Literal["none", "metrics", "all"] = "metrics"
    excluded_metrics: list[str] | None = None
    effect_start: str | None = None
    effect_end: str | None = None
    affected_domains: list[str] | None = None
    cause_note: str | None = None
    resolved_at: str | None = None


class AnomalyPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    impact: int | None = Field(default=None, ge=1, le=5)
    one_off: bool | None = None
    exclusion_mode: Literal["none", "metrics", "all"] | None = None
    excluded_metrics: list[str] | None = None
    effect_start: str | None = None
    effect_end: str | None = None
    cause_note: str | None = None
    resolved_at: str | None = None


class AnomalyExclusionPatch(BaseModel):
    """PATCH /anomalies/{id}/exclusion：排除方式、指标范围与观察期（v0.2）。"""

    model_config = ConfigDict(extra="ignore")

    exclusion_mode: Literal["none", "metrics", "all"]
    excluded_metrics: list[str] | None = None
    effect_start: str | None = None
    effect_end: str | None = None


# ---------------------------------------------------------------------------
# 关系边
# ---------------------------------------------------------------------------
class LinkCreate(BaseModel):
    src_type: Literal["entry", "goal", "plan", "person", "decision"]
    src_id: str
    dst_type: Literal["entry", "goal", "plan", "person", "decision"]
    dst_id: str
    relation: str
    created_by: Literal["user", "rule", "ai"] = "user"
    confirmed: bool | None = None
    strength: float | None = None
    evidence: Any = None


# ---------------------------------------------------------------------------
# 长期对象
# ---------------------------------------------------------------------------
class PersonCreate(BaseModel):
    display_name: str
    code: str | None = None
    relation_type: str | None = None
    closeness: int | None = Field(default=None, ge=1, le=5)
    since_date: str | None = None
    note: str | None = None


class PersonPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str | None = None
    relation_type: str | None = None
    closeness: int | None = Field(default=None, ge=1, le=5)
    since_date: str | None = None
    note: str | None = None


class GoalCreate(BaseModel):
    title: str
    parent_goal_id: str | None = None
    area: str | None = None
    horizon: Literal["1y", "3y", "5y", "life"] | None = None
    metric_name: str | None = None
    metric_unit: str | None = None
    direction: Literal["increase", "decrease", "maintain"] | None = None
    tolerance: float | None = None
    baseline: float | None = None
    target: float | None = None
    target_date: str | None = None
    status: Literal["active", "paused", "achieved", "dropped", "revised"] | None = None
    why_note: str | None = None


class GoalPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    parent_goal_id: str | None = None
    title: str | None = None
    area: str | None = None
    horizon: Literal["1y", "3y", "5y", "life"] | None = None
    metric_name: str | None = None
    metric_unit: str | None = None
    direction: Literal["increase", "decrease", "maintain"] | None = None
    tolerance: float | None = None
    baseline: float | None = None
    target: float | None = None
    target_date: str | None = None
    status: Literal["active", "paused", "achieved", "dropped", "revised"] | None = None
    why_note: str | None = None


class CheckpointCreate(BaseModel):
    """POST /goals/{id}/checkpoints：目标回看（第 12.2 节）。"""

    metric_value: float | None = None
    confidence: int | None = Field(default=None, ge=1, le=5)
    decision: Literal["keep", "revise", "pause", "drop", "achieve"] | None = None
    progress_note: str | None = None
    title: str | None = None
    note: str | None = None
    sensitivity: int | None = None


class PlanCreate(BaseModel):
    title: str
    goal_id: str | None = None
    cadence: Literal["daily", "weekly", "monthly", "once"] | None = None
    start_date: str | None = None
    end_date: str | None = None
    expected_output: str | None = None
    output_metric: str | None = None
    est_minutes_per_week: int | None = None
    status: Literal["active", "done", "dropped", "replaced"] | None = None
    replaced_by: str | None = None


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goal_id: str | None = None
    title: str | None = None
    cadence: Literal["daily", "weekly", "monthly", "once"] | None = None
    start_date: str | None = None
    end_date: str | None = None
    expected_output: str | None = None
    output_metric: str | None = None
    est_minutes_per_week: int | None = None
    status: Literal["active", "done", "dropped", "replaced"] | None = None
    replaced_by: str | None = None


class PlanRecordCreate(BaseModel):
    """POST /plans/{id}/records：计划打卡（domain=plan）。"""

    planned_units: float | None = None
    actual_units: float | None = None
    unit: str | None = None
    output_value: float | None = None
    outcome: Literal["done", "partial", "skipped", "blocked"] | None = None
    status: Literal["draft", "final"] | None = None
    title: str | None = None
    note: str | None = None


class NamedCreate(BaseModel):
    name: str
    area: str | None = None


class ResourceAccountCreate(BaseModel):
    """POST /resources：资源/资产初始登记（第 5 章）。"""

    name: str
    kind: Literal["time", "energy", "money", "asset", "tool", "space"] | None = None
    unit: str | None = None
    initial_value: float = 0
    note: str | None = None


class SkillLevelCreate(BaseModel):
    level: float
    assessed_at: str
    evidence_entry_id: str | None = None


class EgressPolicyUpdate(BaseModel):
    """PUT /egress/policy：切换出站策略（第 7.4 节「出站策略」表）。"""

    policy: Literal["none", "api_summary", "api_extended"]


class EgressPreviewRequest(BaseModel):
    """POST /egress/preview：按某一用途预览将要出站的内容（第 12.2 节）。"""

    purpose: str
    period: str = "weekly"
    end: str | None = None
    start: str | None = None
    confirmed_texts: list[str] | None = None


class LlmSettingsUpdate(BaseModel):
    """PUT /llm/settings：保存模型供应商设置（第 9.2 节）。

    ``api_key`` 只进系统钥匙串，接口既不回显也不落 config.toml；
    留空表示「保持现有密钥不变」，``clear_api_key`` 才是显式删除。
    """

    provider: str = ""
    model: str = ""
    base_url: str = ""
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    max_retries: int = Field(default=2, ge=0, le=5)
    # 输出 token 上限：不传就沿用当前值，避免只改供应商时把手调过的预算重置成默认
    max_tokens: int | None = Field(default=None, ge=256, le=32000)
    api_key: str | None = None
    clear_api_key: bool = False


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
class ReportCreateRequest(BaseModel):
    """POST /reports：手动生成一份报告（第 12.2 节）。

    ``confirm_child_report``：儿童/青少年档报告由家长显式触发（第 7.7 节、P5 W3），
    必须由监护人会话二次确认，避免系统悄悄替孩子产出报告。
    """

    kind: Literal["weekly", "monthly", "yearly"] = "weekly"
    end: str | None = None
    start: str | None = None
    confirm_child_report: bool = False


class AdoptExperimentRequest(BaseModel):
    """POST /reports/{id}/adopt-experiment：把小实验采纳为计划（第 9.7 节）。"""

    index: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# 备份与恢复（第 7.6 节、FR-17）
# ---------------------------------------------------------------------------
class BackupRequest(BaseModel):
    """POST /backup：打一份加密备份。

    ``dest_dir`` 留空就落到档案自己的 backups/ 目录（保留最近 5 份）；
    指定目录时原样写入，不擅自清理用户目录里的文件。
    """

    dest_dir: str | None = None


class RestoreRequest(BaseModel):
    """POST /restore：用口令或恢复密钥恢复一份备份。

    需要二次确认：输入档案别名，防止手滑把现有数据覆盖掉。
    """

    path: str
    passphrase: str | None = None
    recovery_key: str | None = None
    confirm_alias: str


# ---------------------------------------------------------------------------
# 自我认知校验（第 16 章、P1.5）
# ---------------------------------------------------------------------------
ClaimKind = Literal["strength", "difficulty", "value", "plan_belief", "change"]


class ClaimCreate(BaseModel):
    """POST /claims：记一条对自己的判断。"""

    statement: str
    kind: ClaimKind
    domain: str | None = None
    why_note: str | None = None
    testable_as: str | None = None


class ClaimPatch(BaseModel):
    """PATCH /claims/{id}：确认可检验表述或改状态。"""

    model_config = ConfigDict(extra="ignore")

    testable_as: str | None = None
    status: Literal[
        "open", "supported", "contradicted", "mixed", "insufficient", "retracted"
    ] | None = None


class HypothesisGenerate(BaseModel):
    """POST /claims/{id}/hypotheses：对一条自述跑检测器。

    额外的目标参数（技能、指标、领域）只能补检测器用得上的那一类；
    step 由用户定义「怎样算数」，系统不替用户定义（第 16.5 节第 2 步）。
    """

    as_of: str | None = None
    skill_id: str | None = None
    metric_key: str | None = None
    area: str | None = None


class HypothesisRespond(BaseModel):
    """POST /hypotheses/{id}/respond：接受、驳回或补充背景。"""

    action: Literal["accept", "reject", "context"]
    note: str | None = None


class SelfCheckSettingsUpdate(BaseModel):
    """PUT /selfcheck/settings：模块开关、暂停、每周条数（第 16.9 节）。"""

    model_config = ConfigDict(extra="ignore")

    enabled: bool | None = None
    paused: bool | None = None
    weekly_limit: int | None = Field(default=None, ge=0, le=7)


# ---------------------------------------------------------------------------
# 主题（第 11.3 节、P2）
# ---------------------------------------------------------------------------
class ThemeCreate(BaseModel):
    """POST /themes：保存一套在浏览器算好的主题（亮暗两套 token）。

    ``tokens`` 形如 ``{"light": {...}, "dark": {...}}``，由主题引擎生成；
    ``source_attachment_id`` 指向用作取色来源的加密原图附件（可选）。
    """

    name: str
    tokens: dict[str, Any]
    source_attachment_id: str | None = None
    activate: bool = True


# ---------------------------------------------------------------------------
# 决策与联网研究回路（第 10 章、P3）
# ---------------------------------------------------------------------------
class DecisionCreate(BaseModel):
    question: str
    domain: str | None = None


class DecisionAssess(BaseModel):
    decision_type: str | None = None


class ResearchPaste(BaseModel):
    pasted_text: str


class EvidencePatch(BaseModel):
    user_verified: bool | None = None


