/** API 客户端（第 12 章）。
 *
 * 浏览器只管展示：所有校验以服务端为准（第 6.4 节），
 * 这里只负责带令牌、解析固定三字段错误体、区分 401 与 423。
 */

import { getToken } from './session'
import {
  ApiError,
  type Anomaly,
  type AnomalyDraft,
  type Attachment,
  type BackupItem,
  type BackupResult,
  type BasicsOptions,
  type Entry,
  type EntryFilters,
  type EntryPage,
  type EgressLogItem,
  type EgressPolicyInfo,
  type EgressPreview,
  type FactPackage,
  type FormDef,
  type ErrorBody,
  type Goal,
  type GoalList,
  type GuardianPolicy,
  type HealthInfo,
  type Hobby,
  type HobbyList,
  type Hypothesis,
  type HypothesisGeneration,
  type JudgmentReview,
  type MetricList,
  type Plan,
  type PlanDiagnosis,
  type PlanList,
  type PersonList,
  type Skill,
  type SkillList,
  type ResourceAccount,
  type ResourceAccountList,
  type ProfileBasics,
  type ProfileCreated,
  type ProfileList,
  type Report,
  type ReportListItem,
  type RestoreResult,
  type SelfCheckSettings,
  type SelfClaim,
  type ThemeRecord,
  type UnlockResult,
  type Assessment,
  type AiReview,
  type DecisionCase,
  type DecisionReport,
  type Evidence,
  type PageReview,
  type RelationReviewAnalysis,
  type SelfReviewAnalysis,
  type PromptPack,
  type ResearchTask,
  type ChainData,
  type GraphData,
  type LinkSuggestion,
  type LlmSettings,
  type Person,
  type PersonDetail,
  type PersonLink,
} from './types'

export const API_PREFIX = '/api/v1'

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers = new Headers(init.headers)
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  let response: Response
  try {
    response = await fetch(API_PREFIX + path, { ...init, headers })
  } catch {
    throw new ApiError(0, {
      code: 'network_error',
      message: '无法连接本地核心进程，请确认它仍在运行。',
      details: {},
    })
  }

  if (response.status === 204) {
    return undefined as T
  }

  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = null
    }
  }

  if (!response.ok) {
    const body = (payload ?? {}) as Partial<ErrorBody>
    throw new ApiError(response.status, {
      code: body.code ?? 'unknown_error',
      message: body.message ?? `请求失败（HTTP ${response.status}）`,
      details: body.details ?? {},
    })
  }
  return payload as T
}

export const api = {
  health: () => request<HealthInfo>('/health'),

  listProfiles: () => request<ProfileList>('/profiles'),

  createProfile: (body: {
    alias: string
    passphrase: string
    age_band: string
    birth_year?: number | null
    base_currency?: string
    /** 儿童档必填：监护人已知情并同意（第 7.7 节）。 */
    guardian_consent?: boolean
    /** 可选：监护人口令，留空则不挂 guardian 包装（第 7.2 节第 3 条）。 */
    guardian_passphrase?: string | null
  }) =>
    request<ProfileCreated>('/profiles', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  unlock: (
    profileId: string,
    body: { passphrase?: string; recovery_key?: string; role?: 'owner' | 'guardian' },
  ) =>
    request<UnlockResult>(`/profiles/${profileId}/unlock`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** 挂一枚监护人密钥包装（第 7.7 节）：需本人会话已解锁。 */
  addGuardian: (profileId: string, passphrase: string) =>
    request<{ guardian_linked: boolean }>(`/profiles/${profileId}/guardian`, {
      method: 'POST',
      body: JSON.stringify({ passphrase }),
    }),

  /** 撤销监护人包装（满 14/18 周岁后本人可撤销）。 */
  removeGuardian: (profileId: string) =>
    request<{ guardian_linked: boolean }>(`/profiles/${profileId}/guardian`, { method: 'DELETE' }),

  getGuardianPolicy: (profileId: string) =>
    request<{ policy: GuardianPolicy }>(`/profiles/${profileId}/guardian-policy`),

  setGuardianPolicy: (profileId: string, body: Partial<GuardianPolicy>) =>
    request<{ policy: GuardianPolicy }>(`/profiles/${profileId}/guardian-policy`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  lock: (profileId: string) =>
    request<{ locked: boolean }>(`/profiles/${profileId}/lock`, { method: 'POST' }),

  /** 记录档案的界面语言（NFR-08、P6 W4）：下次解锁沿用这个语言。 */
  setProfileLocale: (profileId: string, locale: string) =>
    request<{ locale: string }>(`/profiles/${profileId}/locale`, {
      method: 'PUT',
      body: JSON.stringify({ locale }),
    }),

  /** 界面内的「退出 LifeOS」（P6 W3.1）：核心进程收到后优雅停服。 */
  shutdown: () => request<{ shutting_down: boolean }>('/system/shutdown', { method: 'POST' }),

  /** 个人基础信息（决策评估与自我认知的前提条件）。 */
  getBasics: () => request<{ item: ProfileBasics; options: BasicsOptions }>('/profile/basics'),
  updateBasics: (body: Partial<ProfileBasics>) =>
    request<{ item: ProfileBasics }>('/profile/basics', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  listForms: (ageBand?: string) =>
    request<{ items: FormDef[] }>(`/forms${ageBand ? `?age_band=${ageBand}` : ''}`),

  listEntries: (filters: EntryFilters = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) {
      if (value === undefined || value === null || value === '') continue
      params.set(key, String(value))
    }
    const query = params.toString()
    return request<EntryPage>(`/entries${query ? `?${query}` : ''}`)
  },

  getEntry: (id: string) => request<Entry>(`/entries/${id}`),

  /** 按表单提交（第 3.5 节 A 录入流）；带 Idempotency-Key 防重复提交。 */
  submitForm: (formId: string, values: Record<string, unknown>) =>
    request<Entry>('/entries', {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ form_id: formId, values }),
    }),

  submitStructured: (payload: Record<string, unknown>) =>
    request<Entry>('/entries', {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify(payload),
    }),

  deleteEntry: (id: string) => request<void>(`/entries/${id}`, { method: 'DELETE' }),

  addAnomaly: (entryId: string, draft: AnomalyDraft) =>
    request<Anomaly>(`/entries/${entryId}/anomaly`, {
      method: 'POST',
      body: JSON.stringify(draft),
    }),

  updateAnomalyExclusion: (
    anomalyId: string,
    body: { exclusion_mode: string; excluded_metrics?: string[] },
  ) =>
    request<Anomaly>(`/anomalies/${anomalyId}/exclusion`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  // -- 分析（第 8 章、第 12.2 节）------------------------------------------
  getFacts: (period = 'weekly', end?: string, start?: string) => {
    const params = new URLSearchParams({ period })
    if (end) params.set('end', end)
    if (start) params.set('start', start)
    return request<FactPackage>(`/facts?${params.toString()}`)
  },

  // -- 长期对象（第 12.2 节）-----------------------------------------------
  listGoals: (status?: string) =>
    request<GoalList>(`/goals${status ? `?status=${status}` : ''}`),

  createGoal: (body: Record<string, unknown>) =>
    request<Goal>('/goals', { method: 'POST', body: JSON.stringify(body) }),

  addGoalCheckpoint: (goalId: string, body: Record<string, unknown>) =>
    request<Entry>(`/goals/${goalId}/checkpoints`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  listPlans: (filters: { status?: string; goal_id?: string } = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) {
      if (value) params.set(key, value)
    }
    const query = params.toString()
    return request<PlanList>(`/plans${query ? `?${query}` : ''}`)
  },

  createPlan: (body: Record<string, unknown>) =>
    request<Plan>('/plans', { method: 'POST', body: JSON.stringify(body) }),

  planDiagnosis: (planId: string, asOf?: string) =>
    request<PlanDiagnosis>(
      `/plans/${planId}/diagnosis${asOf ? `?as_of=${asOf}` : ''}`,
    ),

  /** 计划打卡（第 12.2 节）：写一条 domain=plan 的条目。 */
  addPlanRecord: (planId: string, body: Record<string, unknown>) =>
    request<Entry>(`/plans/${planId}/records`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // -- 爱好与技能（第 5 章 hobby / education / career 表单的选择器）------------
  listHobbies: () => request<HobbyList>('/hobbies'),

  createHobby: (name: string) =>
    request<Hobby>('/hobbies', { method: 'POST', body: JSON.stringify({ name }) }),

  listSkills: () => request<SkillList>('/skills'),

  createSkill: (name: string) =>
    request<Skill>('/skills', { method: 'POST', body: JSON.stringify({ name }) }),

  // -- 资源/资产台账（第 5 章）：初始登记在「资源」页签完成 -----------------
  listResources: () => request<ResourceAccountList>('/resources'),

  createResource: (body: {
    name: string
    kind?: string | null
    unit?: string | null
    initial_value?: number
    note?: string | null
  }) =>
    request<ResourceAccount>('/resources', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // -- 报告（第 9 章、第 12.2 节）------------------------------------------
  listReports: (kind?: string, limit = 50) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (kind) params.set('kind', kind)
    return request<{ items: ReportListItem[] }>(`/reports?${params.toString()}`)
  },

  getReport: (id: string) => request<Report>(`/reports/${id}`),

  createReport: (body: {
    kind: 'weekly' | 'monthly' | 'yearly'
    end?: string
    start?: string
    /** 儿童/青少年档：监护人二次确认后才产出（P5 W3、S5）。 */
    confirm_child_report?: boolean
  }) => request<Report>('/reports', { method: 'POST', body: JSON.stringify(body) }),

  adoptExperiment: (reportId: string, index: number) =>
    request<{ plan: Plan }>(`/reports/${reportId}/adopt-experiment`, {
      method: 'POST',
      body: JSON.stringify({ index }),
    }),

  // -- 模型供应商（第 9.2 节）----------------------------------------------
  /** 读当前设置与供应商目录；密钥只回传「是否已保存」，绝不回显。 */
  getLlmSettings: () => request<LlmSettings>('/llm/settings'),

  /** 保存设置；api_key 留空表示保持原密钥，clear_api_key 才是删除。 */
  updateLlmSettings: (body: {
    provider: string
    model: string
    base_url: string
    timeout_seconds: number
    max_retries: number
    api_key?: string
    clear_api_key?: boolean
  }) =>
    request<LlmSettings>('/llm/settings', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  // -- 出站与隐私（第 7.4 节、第 12.2 节）----------------------------------
  getEgressPolicy: () => request<EgressPolicyInfo>('/egress/policy'),

  setEgressPolicy: (policy: string) =>
    request<{ policy: string }>('/egress/policy', {
      method: 'PUT',
      body: JSON.stringify({ policy }),
    }),

  listEgressLog: (limit = 50) =>
    request<{ items: EgressLogItem[] }>(`/egress/log?limit=${limit}`),

  previewEgress: (body: {
    purpose: string
    period?: string
    end?: string
    start?: string
    confirmed_texts?: string[]
  }) =>
    request<EgressPreview>('/egress/preview', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // -- 备份与恢复（第 7.6 节、第 12.2 节）----------------------------------
  listBackups: () => request<{ items: BackupItem[] }>('/backups'),

  createBackup: (destDir?: string) =>
    request<BackupResult>('/backup', {
      method: 'POST',
      body: JSON.stringify({ dest_dir: destDir ?? null }),
    }),

  restoreBackup: (body: {
    path: string
    passphrase?: string
    recovery_key?: string
    confirm_alias: string
  }) =>
    request<RestoreResult>('/restore', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  // -- 自我认知校验（第 16 章、第 12.2 节）---------------------------------
  getSelfCheckSettings: () => request<SelfCheckSettings>('/selfcheck/settings'),

  updateSelfCheckSettings: (body: {
    enabled?: boolean
    paused?: boolean
    weekly_limit?: number
  }) =>
    request<SelfCheckSettings>('/selfcheck/settings', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  listClaims: (status?: string) =>
    request<{ items: SelfClaim[] }>(`/claims${status ? `?status=${status}` : ''}`),

  createClaim: (body: {
    statement: string
    kind: string
    domain?: string | null
    why_note?: string | null
    testable_as?: string | null
  }) => request<SelfClaim>('/claims', { method: 'POST', body: JSON.stringify(body) }),

  updateClaim: (claimId: string, body: { testable_as?: string; status?: string }) =>
    request<SelfClaim>(`/claims/${claimId}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),

  deleteClaim: (claimId: string) =>
    request<{ ok: boolean }>(`/claims/${claimId}`, { method: 'DELETE' }),

  /** 跑检测器：可能返回假设，也可能只返回数据缺口或被安全阀拦住。 */
  generateHypotheses: (
    claimId: string,
    body: { skill_id?: string; metric_key?: string; area?: string } = {},
  ) =>
    request<HypothesisGeneration>(`/claims/${claimId}/hypotheses`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  listHypotheses: (filters: { claim_id?: string; status?: string } = {}) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) {
      if (value) params.set(key, value)
    }
    const query = params.toString()
    return request<{ items: Hypothesis[] }>(`/hypotheses${query ? `?${query}` : ''}`)
  },

  respondHypothesis: (
    hypothesisId: string,
    body: { action: 'accept' | 'reject' | 'context'; note?: string },
  ) =>
    request<Hypothesis>(`/hypotheses/${hypothesisId}/respond`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getJudgments: (limit = 100) =>
    request<JudgmentReview>(`/judgments?limit=${limit}`),

  /** 自我认知 AI 解读：上次结果（没有则 item 为 null）。 */
  getSelfCheckAiReview: () =>
    request<{ item: PageReview<SelfReviewAnalysis> | null }>('/selfcheck/ai-review'),

  /** 自我认知 AI 解读：按需调用模型；不可用时返回 degraded 与原因。 */
  aiReviewSelfCheck: () =>
    request<PageReview<SelfReviewAnalysis>>('/selfcheck/ai-review', { method: 'POST' }),

  // -- 附件（第 12.2 节）--------------------------------------------------
  /** 上传附件：加密落在档案目录，只回元数据（第 7.2 节第 5 条）。 */
  uploadAttachment: (file: File, kind = 'image') => {
    const form = new FormData()
    form.append('file', file)
    form.append('kind', kind)
    return request<Attachment>('/attachments', { method: 'POST', body: form })
  },

  // -- 指标（第 12.2 节、仪表盘）-------------------------------------------
  /** 取指标；keys 缺省返回全部，趋势图按单指标取数以避开全量计算。 */
  listMetrics: (period = 'weekly', end?: string, keys?: string[]) => {
    const params = new URLSearchParams({ period })
    if (end) params.set('end', end)
    if (keys && keys.length > 0) params.set('keys', keys.join(','))
    return request<MetricList>(`/metrics?${params.toString()}`)
  },

  // -- 主题（第 11.3 节、P2）-----------------------------------------------
  listThemes: () => request<{ items: ThemeRecord[] }>('/themes'),

  createTheme: (body: {
    name: string
    tokens: ThemeRecord['tokens']
    source_attachment_id?: string | null
    activate?: boolean
  }) => request<ThemeRecord>('/themes', { method: 'POST', body: JSON.stringify(body) }),

  activateTheme: (themeId: string) =>
    request<ThemeRecord>(`/themes/${themeId}/activate`, { method: 'PUT' }),

  // -- 决策与联网研究回路（第 10 章、P3）------------------------------------
  listDecisions: () => request<{ items: DecisionCase[] }>('/decisions'),

  createDecision: (question: string, domain?: string) =>
    request<DecisionCase>('/decisions', {
      method: 'POST',
      body: JSON.stringify({ question, domain }),
    }),

  getDecision: (caseId: string) => request<DecisionCase>(`/decisions/${caseId}`),

  assessDecision: (caseId: string, decisionType?: string) =>
    request<{ assessment: Assessment }>(`/decisions/${caseId}/assess`, {
      method: 'POST',
      body: JSON.stringify({ decision_type: decisionType }),
    }),

  getPromptPack: (caseId: string) =>
    request<PromptPack>(`/decisions/${caseId}/pack`),

  listDecisionTasks: (caseId: string) =>
    request<{ items: ResearchTask[] }>(`/decisions/${caseId}/tasks`),

  pasteResearch: (taskId: string, pastedText: string) =>
    request<{ evidence: Evidence[] }>(`/research-tasks/${taskId}/paste`, {
      method: 'POST',
      body: JSON.stringify({ pasted_text: pastedText }),
    }),

  listEvidence: (caseId: string) =>
    request<{ items: Evidence[] }>(`/decisions/${caseId}/evidence`),

  patchEvidence: (evidenceId: string, userVerified: boolean) =>
    request<Evidence>(`/evidence/${evidenceId}`, {
      method: 'PATCH',
      body: JSON.stringify({ user_verified: userVerified }),
    }),

  synthesizeDecision: (caseId: string) =>
    request<DecisionReport>(`/decisions/${caseId}/synthesize`, { method: 'POST' }),

  /** AI 解读（第 10.6 节）：出站前过隐私网关；模型不可用时返回本地降级结果。 */
  aiReviewDecision: (caseId: string) =>
    request<AiReview>(`/decisions/${caseId}/ai-review`, { method: 'POST' }),

  // -- 关系图谱（第 11.1 节「关系/因果链」、P4）-----------------------------
  listPeople: () => request<PersonList>('/people'),

  createPerson: (displayName: string) =>
    request<Person>('/people', {
      method: 'POST',
      body: JSON.stringify({ display_name: displayName }),
    }),

  getPersonDetail: (personId: string) =>
    request<PersonDetail>(`/people/${personId}/detail`),

  getGraph: () => request<GraphData>('/graph'),

  listLinkSuggestions: () =>
    request<{ items: LinkSuggestion[] }>('/links/suggestions'),

  runLinkSuggest: () =>
    request<{ created: number; ids: string[] }>('/links/suggest', { method: 'POST' }),

  confirmLink: (linkId: string) =>
    request<PersonLink>(`/links/${linkId}/confirm`, { method: 'POST' }),

  rejectLink: (linkId: string) =>
    request<PersonLink>(`/links/${linkId}/reject`, { method: 'POST' }),

  getChain: (entryId: string, depth = 6) =>
    request<ChainData>(`/entries/${entryId}/chain?depth=${depth}`),

  /** 关系 AI 解读：上次结果（没有则 item 为 null）。 */
  getRelationAiReview: () =>
    request<{ item: PageReview<RelationReviewAnalysis> | null }>('/relations/ai-review'),

  /** 关系 AI 解读：只发代号化聚合数字；不可用时返回 degraded 与原因。 */
  aiReviewRelations: () =>
    request<PageReview<RelationReviewAnalysis>>('/relations/ai-review', { method: 'POST' }),
}
