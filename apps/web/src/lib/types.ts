/** 与后端契约对应的类型（第 12 章 API 设计）。 */

export interface ErrorBody {
  code: string
  message: string
  details: Record<string, unknown>
}

/** 错误体固定三字段：code、message、details（第 12.1 节）。 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>

  constructor(status: number, body: ErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.details = body.details ?? {}
  }

  get locked(): boolean {
    return this.status === 423
  }

  get unauthorized(): boolean {
    return this.status === 401
  }
}

export interface HealthInfo {
  status: string
  version: string
  unlocked: boolean
  profile_id: string | null
  alias: string | null
  idle_lock_minutes: number
}

/** 手机访问：关闭时只有 active=false；开启后带可扫码的地址与剩余秒数。 */
export interface LanStatus {
  active: boolean
  host?: string
  port?: number
  url?: string
  expires_in_seconds?: number
}

export interface ProfileSummary {
  id: string
  alias: string
  created_at: string
}

export interface ProfileList {
  items: ProfileSummary[]
  active_profile_id: string | null
}

export interface ProfileCreated {
  id: string
  alias: string
  recovery_key: string
  notice: string
}

export interface UnlockResult {
  profile_id: string
  alias: string
  age_band: AgeBand
  /** 本次解锁的角色（第 7.7 节）：guardian 会话下读路径按可见范围过滤。 */
  role: UnlockRole
  /** 这个档案是否挂着 guardian 密钥包装。 */
  guardian_linked: boolean
  locale: string
  /** 出生年份（第 6.4 节；早期档案或未填时为空）。用于精确判断是否已跨 14 岁。 */
  birth_year?: number | null
  egress_policy: string
  idle_lock_minutes: number
}

export type UnlockRole = 'owner' | 'guardian' | 'recovery'

/** 监护人可见范围策略（第 7.7 节；应用层约定，非密码学隔离）。 */
export interface GuardianPolicy {
  visible_domains: string[]
  allow_journal_text: boolean
  allow_detail: boolean
}

/** 个人基础信息（决策评估与自我认知的前提条件）。 */
export interface ProfileBasics {
  job_title: string | null
  gender: string | null
  /** 年龄由出生年份推算，只读（改年龄走档案信息）。 */
  age: number | null
  height_cm: number | null
  weight_kg: number | null
  industry: string | null
  education_level: string | null
  work_style: string | null
  risk_tolerance: string | null
  family_status: string | null
  dependents: number | null
  constraints: string[]
  updated_at: string | null
}

export interface BasicsOptions {
  gender: string[]
  industry: string[]
  education_level: string[]
  work_style: string[]
  risk_tolerance: string[]
  family_status: string[]
  constraints: string[]
}

export type AgeBand = 'child' | 'teen' | 'adult' | 'senior'

export interface FieldDef {
  key: string
  type: string
  target: string | null
  ages: string[] | null
  widget: Record<string, string>
  unit: string | null
  options: string[] | { key: string; label: string }[] | string | null
  show_if: string | null
  sensitivity_default: number | null
  min: number | null
  max: number | null
  step: number | null
}

export interface SectionDef {
  id: string
  fields: FieldDef[]
}

export interface FormDef {
  id: string
  domain: string
  version: number
  title: string
  anomaly: boolean
  sections: SectionDef[]
}

export interface Anomaly {
  id: string
  entry_id: string
  type: string
  impact: number
  one_off: boolean
  exclusion_mode: 'none' | 'metrics' | 'all'
  excluded_metrics: string[]
  affected_domains: string[]
  effect_start: string | null
  effect_end: string | null
  cause_note: string | null
  resolved_at: string | null
  created_at: string
}

export interface AnomalyDraft {
  type: string
  impact: number
  cause_note?: string | null
  exclusion_mode?: 'none' | 'metrics' | 'all'
}

export interface Entry {
  id: string
  domain: string
  occurred_at: string
  occurred_date: string
  title: string | null
  note: string | null
  tags: string[]
  extra: Record<string, unknown>
  sensitivity: number
  visibility: string
  status: 'draft' | 'final'
  source: string
  created_at: string
  updated_at: string
  detail: Record<string, unknown>
  anomalies: Anomaly[]
  links: unknown[]
}

export interface EntryPage {
  items: Entry[]
  next_cursor: string | null
  /** guardian 会话下被可见范围挡掉的条目数（第 7.7 节、验收 A2）。 */
  hidden_count?: number
}

export interface EntryFilters {
  domain?: string
  date_from?: string
  date_to?: string
  q?: string
  tag?: string
  person_id?: string
  has_anomaly?: boolean
  limit?: number
  cursor?: string
}

export interface SubmitResult {
  ok: boolean
  entry?: Entry
  error?: ApiError
}

/** 后端 i18n 消息对象：{key, params}，由渲染层本地化（NFR-08）。 */
export interface MessageObject {
  key: string
  params: Record<string, unknown>
}

/** 出站载荷里的金额字段在出站前被换算成「占收入比例」（第 7.4 节）。 */
export interface ExcludedEntry {
  entry_id: string
  date: string
  reason: string
}

export interface MetricLineage {
  source: string | null
  formula_id: string | null
  formula_params: Record<string, unknown>
  excluded: ExcludedEntry[]
  thresholds_ref: string | null
  corrections: unknown[]
}

export interface MetricLine {
  key: string
  description: string
  unit: string | null
  value_kind: string | null
  value: number | null
  prev_value: number | null
  delta: number | null
  n: number
  status: string
  reason: MessageObject | string | null
  excluded_anomalies: number
  value_without_exclusion: number | null
  lineage: MetricLineage
}

/** GET /metrics：某一周期的一组指标（仪表盘的卡片与趋势都取这里）。 */
export interface MetricList {
  period: { kind: string; start: string; end: string }
  items: MetricLine[]
}

export interface PlanDiagnosis {
  verdict: string
  reasons: MessageObject[]
  thresholds_ref: unknown
  user_rejected: boolean
}

export interface FactPlan {
  ref: string
  completion_rate: number | null
  anomaly_share: number | null
  output_trend: string
  diagnosis: PlanDiagnosis
}

export interface FactPackage {
  facts_version: number
  period: { kind: string; start: string; end: string }
  profile: Record<string, unknown>
  coverage: Record<string, string>
  metrics: MetricLine[]
  domain_facts_version: number
  domain_facts: Record<string, Record<string, unknown>>
  plans: FactPlan[]
  insights: unknown[]
  anomalies: { count: number; impact_sum: number }
  data_gaps: unknown[]
  /** 月报/年报的跨月对比：逐月聚合数字（本地计算，不联网）。 */
  history?: HistoryPoint[]
}

export interface HistoryPoint {
  month: string
  record_completeness: number | null
  expense_total: number | null
  mood_mean: number | null
  exercise_minutes: number | null
  relation_entries?: number | null
  career_minutes_total?: number | null
  education_minutes_total?: number | null
  hobby_minutes_total?: number | null
}

export interface ReportFinding {
  claim?: string
  rendered_claim?: string
  figures?: { ref: string; format: string }[]
  evidence_refs?: string[]
  strength?: string
  causal_claim?: boolean
  alternatives?: string[]
  verdict?: string
  experiment?: Experiment
}

export interface Experiment {
  action: string
  window_days?: number
  success_criteria?: string
}

export interface DegradedMetric {
  key: string | null
  text: string
}

export interface DegradedPlan {
  ref: string | null
  verdict: string
  text: string
}

/** 降级报告里的领域摘要行（过程统计，本地生成）。 */
export interface DegradedDomain {
  domain: string
  text: string
}

/** 报告结构化输出：正常报告是「章节名 → 结论数组」，降级报告见 degraded 分支。 */
export interface ReportOutput {
  degraded?: boolean
  reason?: MessageObject
  metrics?: DegradedMetric[]
  domain_facts?: DegradedDomain[]
  plans?: DegradedPlan[]
  data_gaps?: unknown[]
  [section: string]: unknown
}

export interface ReportListItem {
  id: string
  kind: string
  period_start: string | null
  period_end: string | null
  status: string
  model: string
  template_id: string
  template_hash: string
  snapshot_hash: string
  summary_md: string
  created_at: string
  /** 溯源信息（NFR-06）：生成这份报告时用的提示词/指标版本与指纹，用于复现。 */
  template_version: number | null
  metrics_version: string
  metrics_hash: string
}

export interface Report extends ReportListItem {
  facts: FactPackage | null
  output: ReportOutput | null
  egress_log_id: string | null
}

// ---------------------------------------------------------------------------
// 长期对象（第 12.2 节）
// ---------------------------------------------------------------------------
export interface Goal {
  id: string
  parent_goal_id: string | null
  title: string
  area: string | null
  horizon: string | null
  metric_name: string | null
  metric_unit: string | null
  direction: string | null
  tolerance: number | null
  baseline: number | null
  target: number | null
  target_date: string | null
  status: string
  why_note: string | null
  created_at: string
  updated_at: string
}

export interface Plan {
  id: string
  goal_id: string | null
  title: string
  cadence: string | null
  start_date: string | null
  end_date: string | null
  expected_output: string | null
  output_metric: string | null
  est_minutes_per_week: number | null
  status: string
  replaced_by: string | null
  created_at: string
  updated_at: string
}

export interface GoalList {
  items: Goal[]
}

export interface PlanList {
  items: Plan[]
}

/** 爱好与技能（第 5 章）：供录入表单的 hobby / skill 选择器使用。 */
export interface Hobby {
  id: string
  name: string
  created_at: string
}

export interface Skill {
  id: string
  name: string
  area: string | null
  created_at: string
}

export interface HobbyList {
  items: Hobby[]
}

export interface SkillList {
  items: Skill[]
}

export interface PersonList {
  items: Person[]
}

/** 资源/资产台账（第 5 章）：先登记账户，之后录入只记变化量。 */
export interface ResourceAccount {
  id: string
  name: string
  kind: string | null
  unit: string | null
  current_value: number
  note: string | null
  created_at: string
  updated_at: string
}

export interface ResourceAccountList {
  items: ResourceAccount[]
}

// ---------------------------------------------------------------------------
// 出站与隐私（第 7.4 节）
// ---------------------------------------------------------------------------
export interface EgressPolicyInfo {
  policy: string
  age_band: string
  locked: boolean
  choices: string[]
}

export interface EgressLogItem {
  id: string
  ts: string
  purpose: string
  provider: string | null
  model: string | null
  policy: string | null
  payload_sha256: string
  payload_bytes: number | null
  status: string | null
  cost_est: number | null
  redaction_report: Record<string, unknown>
}

export interface EgressPreview {
  purpose: string
  policy: string
  allowed: boolean
  reason: MessageObject | string
  payload: Record<string, unknown>
  report: {
    dropped: string[]
    dropped_count: number
    hits: Record<string, number>
    transforms: Record<string, number>
  }
  sha256: string
  nbytes: number
  log_id: string
}

// ---------------------------------------------------------------------------
// 加密备份与恢复（第 7.6 节、第 12.2 节）
// ---------------------------------------------------------------------------
/** 备份列表项：readable=false 表示头部读不出来（多半已损坏）。 */
export interface BackupItem {
  path: string
  name: string
  size_bytes: number
  created_at: string
  alias: string
  readable: boolean
}

export interface BackupResult {
  path: string
  profile_id: string
  alias: string
  created_at: string
  size_bytes: number
  db_sha256: string
  blob_count: number
}

export interface RestoreResult {
  profile_id: string
  alias: string
  db_sha256: string
  blob_count: number
}

// ---------------------------------------------------------------------------
// 附件（第 12.2 节）
// ---------------------------------------------------------------------------
export interface Attachment {
  id: string
  entry_id: string | null
  kind: string
  blob_name: string
  mime: string | null
  size_bytes: number | null
  sha256: string
  created_at: string
}

// ---------------------------------------------------------------------------
// 主题（第 11.3 节、P2）
// ---------------------------------------------------------------------------
export interface ThemeRecord {
  id: string
  name: string
  tokens: { light: Record<string, string>; dark: Record<string, string> }
  source_attachment_id: string | null
  is_active: boolean
  created_at: string
}

// ---------------------------------------------------------------------------
// 自我认知校验（第 16 章、第 12.2 节 v0.2）
// ---------------------------------------------------------------------------
/** 检测器事实/覆盖项：key 是 i18n 键，渲染时本地化。 */
export interface SelfCheckFact {
  key: string
  value: unknown
  unit: string | null
}

export interface SelfCheckCoverage {
  days: number
  items: SelfCheckFact[]
  /** 数据缺口（key + params 的 i18n 消息对象）。 */
  missing: MessageObject[]
}

export interface SelfClaim {
  id: string
  statement: string
  kind: string
  domain: string | null
  why_note: string | null
  testable_as: string | null
  status: string
  made_at: string
  updated_at: string
  deleted_at: string | null
}

export interface HypothesisAlternative {
  text: string
  compatible_with_claim?: boolean
}

export interface HypothesisEvidence {
  id: string
  hypothesis_id: string
  kind: string
  ref_type: string
  ref_id: string
  /** 规则生成的说明是 {key, params} 消息对象；用户补充的背景是自由文本。 */
  note: string | MessageObject | null
  added_by: string
  created_at: string
}

// -- 决策与联网研究回路（第 10 章、P3）--------------------------------------
export interface KnowledgeGap {
  id: string
  question: string
  why?: string
  freshness_days?: number
  requires_authority?: boolean
  serves_criteria?: string
}

export interface Criterion {
  name: string
  key: string
  default_weight: number
  direction: 'higher_is_better' | 'lower_is_better'
}

export interface Assessment {
  decision_type: string
  risk_level: 'low' | 'medium' | 'high'
  internal_facts: Array<{ key: string; description: string; value: unknown; source: string }>
  knowledge_gaps: KnowledgeGap[]
  criteria: Criterion[]
  skip_research: boolean
  warnings: string[]
  authority_hint?: string
}

export interface DecisionCase {
  id: string
  question: string
  domain: string | null
  status: 'draft' | 'assessing' | 'researching' | 'synthesized' | 'decided' | 'reviewed'
  assessment_json: Assessment | null
  criteria_json: unknown
  decision_note: string | null
  review_at: string | null
  ai_review_json: AiReview | null
  created_at: string
  updated_at: string
}

export interface ResearchTask {
  id: string
  case_id: string
  pack_id: string
  question_key: string
  prompt_text: string
  status: 'pending' | 'pasted' | 'ingested' | 'verified'
  pasted_text: string | null
  pasted_at: string | null
}

export interface PromptPack {
  pack_id: string | null
  tasks: ResearchTask[]
  preview: Array<{ question_key: string; prompt_text: string }>
}

export interface Evidence {
  id: string
  task_id: string
  claim: string
  data_value: string | null
  unit: string | null
  source_name: string | null
  source_url: string | null
  source_date: string | null
  source_type: 'official' | 'research' | 'media' | 'experience' | 'unknown'
  confidence: 'high' | 'medium' | 'low'
  conflicts_with: string | null
  stale: boolean
  user_verified: boolean
}

export interface DecisionReport {
  report_version: number
  case_id: string
  question: string
  risk_level: string
  evidence_quality: {
    total: number
    by_source_type: Record<string, number>
    unverified: number
    stale: number
  }
  options_matrix: Array<{
    criterion_key: string
    criterion_name: string
    items: Array<{
      evidence_id: string | null
      claim: string
      weight: number
      verified: boolean
      stale: boolean
    }>
  }>
  key_assumptions: string[]
  reversible_experiments: string[]
  conflicting_evidence: Array<{ evidence_id: string; conflicts_with: string | null }>
  missing_information: string[]
  unverified_evidence_ids: string[]
  downweight_note: string
  high_risk_notice?: string
}

/** AI 解读（第 10.6 节）：ok 时给 analysis，degraded 时给本地 fallback 与原因。 */
export interface AiReviewAnalysis {
  summary?: ReportFinding[]
  support?: ReportFinding[]
  oppose?: ReportFinding[]
  assumptions?: ReportFinding[]
  risks?: ReportFinding[]
  next_steps?: string[]
}

export interface AiReview {
  status: 'ok' | 'degraded'
  template_version: number
  template_hash: string
  model: string
  egress_log_id: string
  locale: string
  reviewed_at: string
  analysis?: AiReviewAnalysis
  fallback?: DecisionReport
  reason?: MessageObject
}

/** 页面级 AI 解读（自我认知第 16 章 / 关系第 11.1 节）：ok 给 analysis，degraded 给原因。 */
export interface SelfReviewAnalysis {
  headline?: string
  observations?: ReportFinding[]
  tensions?: ReportFinding[]
  questions?: string[]
  experiments?: Experiment[]
  uncertainty?: string[]
}

export interface RelationReviewAnalysis {
  headline?: string
  patterns?: ReportFinding[]
  attention?: ReportFinding[]
  suggestions?: Experiment[]
  uncertainty?: string[]
}

export interface PageReview<T> {
  status: 'ok' | 'degraded'
  template_version: number
  template_hash: string
  model: string
  egress_log_id: string
  locale: string
  reviewed_at: string
  analysis?: T
  reason?: MessageObject
}

export interface Hypothesis {
  id: string
  claim_id: string | null
  pattern_key: string
  statement: string
  alternatives: HypothesisAlternative[]
  falsifier: string
  strength: string
  coverage: SelfCheckCoverage
  status: string
  proposed_at: string
  reviewed_at: string | null
  facts_snapshot_hash: string
  evidence: HypothesisEvidence[]
}

/** POST /claims/{id}/hypotheses 的结果：出假设、或只出缺口/被安全阀拦住。 */
export interface HypothesisGeneration {
  hypotheses: Hypothesis[]
  gaps: MessageObject[]
  blocked: MessageObject | null
}

export interface JudgmentStats {
  total: number
  counts: Record<string, number>
  shares: Record<string, number>
}

export interface SelfCheckSettings {
  age_band: string
  enabled: boolean
  paused: boolean
  weekly_limit: number
  paused_at: string | null
  /** 是否允许该年龄档使用（child/teen 为 false）。 */
  allowed: boolean
  pause: MessageObject | null
  judgment_stats: JudgmentStats
}

export interface JudgmentLogItem {
  id: string
  source: string
  source_id: string | null
  statement: string
  made_at: string
  check_after: string | null
  outcome: string
  outcome_note: string | null
  outcome_at: string | null
}

export interface JudgmentReview {
  stats: JudgmentStats
  items: JudgmentLogItem[]
}

// -- 关系图谱（第 11.1 节「关系/因果链」、P4）--------------------------------
export interface Person {
  id: string
  display_name: string
  relation_type: string | null
  closeness: number | null
  since_date: string | null
  note: string | null
}

export interface PersonLink {
  id: string
  src_type: string
  src_id: string
  dst_type: string
  dst_id: string
  relation: string
  created_by: string
  confirmed: boolean
  other_label?: string
}

export interface PersonDetail extends Person {
  links: PersonLink[]
}

export interface GraphNode {
  id: string
  label: string
  relation_type: string | null
  closeness: number | null
}

export interface GraphEdge {
  src: string
  dst: string
  kind: string
  weight: number
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface LinkSuggestion {
  id: string
  src_type: string
  src_id: string
  dst_type: string
  dst_id: string
  relation: string
  created_by: string
  confirmed: boolean
  src_label: string
  dst_label: string
  evidence?: Record<string, unknown>
}

export interface ChainNode {
  id: string
  depth: number
  label: string
  domain: string | null
  occurred_date: string | null
}

export interface ChainEdge {
  id: string
  src: string
  dst: string
  relation: string
}

export interface ChainData {
  root: string
  max_depth: number
  reachable_depth: number
  nodes: ChainNode[]
  edges: ChainEdge[]
}

// -- 模型供应商设置（第 9.2 节）---------------------------------------------
export interface LlmProviderInfo {
  id: string
  label_key: string
  default_base_url: string
  models: string[]
  local: boolean
  requires_key: boolean
  docs_url: string
}

export type LlmStatus = 'ready' | 'missing_key' | 'not_configured'

export interface LlmSettings {
  provider: string
  model: string
  base_url: string
  timeout_seconds: number
  max_retries: number
  /** 输出 token 上限：报告篇幅的唯一约束（第 9.2 节）。 */
  max_tokens: number
  api_key_set: boolean
  status: LlmStatus
  providers: LlmProviderInfo[]
}
