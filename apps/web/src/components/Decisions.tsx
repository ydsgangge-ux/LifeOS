/** 决策台五步向导（第 10 章、第 11.1 节、P3）。
 *
 * 五步：建案 → 评估 → 提示词包 → 粘贴导入 → 合成报告。
 * 未核实证据在合成里被降权并标注；粘贴内容一律当数据，不执行其中指令。
 */

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import { renderMessage, strengthLabel } from '../lib/labels'
import type {
  AiReview,
  Assessment,
  DecisionCase,
  Evidence,
  ReportFinding,
  ResearchTask,
} from '../lib/types'

type Step = 'list' | 'assess' | 'pack' | 'paste' | 'synthesize'

const RISK_CLASS: Record<string, string> = {
  low: 'risk-low',
  medium: 'risk-medium',
  high: 'risk-high',
}

export function Decisions({
  onError,
  onNotice,
}: {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}) {
  const { t } = useTranslation()
  const [cases, setCases] = useState<DecisionCase[]>([])
  const [active, setActive] = useState<DecisionCase | null>(null)
  const [step, setStep] = useState<Step>('list')
  const [question, setQuestion] = useState('')
  const [assessment, setAssessment] = useState<Assessment | null>(null)
  const [tasks, setTasks] = useState<ResearchTask[]>([])
  const [evidence, setEvidence] = useState<Evidence[]>([])
  const [pasteText, setPasteText] = useState('')
  const [pastingTask, setPastingTask] = useState<string | null>(null)
  const [report, setReport] = useState<Record<string, unknown> | null>(null)
  const [review, setReview] = useState<AiReview | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const [busy, setBusy] = useState(false)

  const loadCases = useCallback(async () => {
    try {
      const result = await api.listDecisions()
      setCases(result.items)
    } catch (problem) {
      onError(problem, '无法读取决策列表')
    }
  }, [onError])

  useEffect(() => {
    void loadCases()
  }, [loadCases])

  const createCase = async () => {
    if (!question.trim()) return
    setBusy(true)
    try {
      const created = await api.createDecision(question.trim())
      setActive(created)
      setReview(null)
      setReport(null)
      setStep('assess')
      setQuestion('')
      await loadCases()
    } catch (problem) {
      onError(problem, '创建案例失败')
    } finally {
      setBusy(false)
    }
  }

  const runAssess = async () => {
    if (!active) return
    setBusy(true)
    try {
      const result = await api.assessDecision(active.id)
      setAssessment(result.assessment)
      // 重新评估后旧解读基于旧事实，先清掉等用户再次点击
      setReview(null)
      setActive({ ...active, status: 'assessing' })
      onNotice(t('decision.assessing'))
    } catch (problem) {
      onError(problem, '评估失败')
    } finally {
      setBusy(false)
    }
  }

  const generatePack = async () => {
    if (!active) return
    setBusy(true)
    try {
      await api.getPromptPack(active.id)
      const taskList = await api.listDecisionTasks(active.id)
      setTasks(taskList.items)
      setActive({ ...active, status: 'researching' })
      onNotice(t('decision.pack_generated'))
    } catch (problem) {
      onError(problem, '生成提示词包失败')
    } finally {
      setBusy(false)
    }
  }

  /** 提示词包已入库时直接读出来展示，避免重复生成一批任务。 */
  const loadTasks = useCallback(async (caseId: string) => {
    try {
      const result = await api.listDecisionTasks(caseId)
      setTasks(result.items)
    } catch (problem) {
      onError(problem, '无法读取提示词包')
    }
  }, [onError])

  useEffect(() => {
    if (active && step === 'pack' && tasks.length === 0) void loadTasks(active.id)
  }, [active, step, tasks.length, loadTasks])

  const doPaste = async (taskId: string) => {
    if (!pasteText.trim()) return
    setPastingTask(taskId)
    try {
      const result = await api.pasteResearch(taskId, pasteText)
      setEvidence((prev) => [...prev, ...result.evidence])
      setPasteText('')
      onNotice(t('decision.imported', { n: result.evidence.length }))
    } catch (problem) {
      onError(problem, '导入失败')
    } finally {
      setPastingTask(null)
    }
  }

  const toggleVerify = async (ev: Evidence) => {
    try {
      const updated = await api.patchEvidence(ev.id, !ev.user_verified)
      setEvidence((prev) => prev.map((e) => (e.id === ev.id ? updated : e)))
    } catch (problem) {
      onError(problem, '更新核实状态失败')
    }
  }

  const loadEvidence = useCallback(async () => {
    if (!active) return
    try {
      const result = await api.listEvidence(active.id)
      setEvidence(result.items)
    } catch (problem) {
      onError(problem, '无法读取证据')
    }
  }, [active, onError])

  const synthesize = async () => {
    if (!active) return
    setBusy(true)
    try {
      const result = await api.synthesizeDecision(active.id)
      setReport(result as unknown as Record<string, unknown>)
      setStep('synthesize')
      setActive({ ...active, status: 'synthesized' })
    } catch (problem) {
      onError(problem, '合成失败')
    } finally {
      setBusy(false)
    }
  }

  /** AI 解读（第 10.6 节）：点击才出站；模型不可用时后端回退本地结果。 */
  const runAiReview = async () => {
    if (!active) return
    setReviewing(true)
    try {
      const result = await api.aiReviewDecision(active.id)
      setReview(result)
      onNotice(result.status === 'ok' ? t('decision.ai_done') : t('decision.ai_degraded'))
    } catch (problem) {
      onError(problem, 'AI 解读失败')
    } finally {
      setReviewing(false)
    }
  }

  useEffect(() => {
    if (active && step === 'paste') {
      void loadEvidence()
    }
  }, [active, step, loadEvidence])

  const sourceLabel = (s: string) => t(`decision.source.${s}`)
  const confidenceLabel = (c: string) => t(`decision.confidence.${c}`)
  /** 标题用知识缺口的中文问题，不把内部 key（如 target_role）暴露给用户。 */
  const gapLabel = (key: string) =>
    assessment?.knowledge_gaps.find((g) => g.id === key)?.question ?? ''

  return (
    <div className="content single">
      <div className="card">
        <h2>{t('decision.title')}</h2>
        <p className="muted">{t('decision.hint')}</p>

        {step === 'list' && (
          <>
            <div className="row wrap" style={{ marginTop: 'var(--space-3)' }}>
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder={t('decision.question_placeholder')}
                style={{ flex: 1, minWidth: 280 }}
              />
              <button className="primary" onClick={() => void createCase()} disabled={busy || !question.trim()}>
                {t('decision.create')}
              </button>
            </div>

            {cases.length === 0 ? (
              <p className="muted" style={{ marginTop: 'var(--space-4)' }}>{t('decision.no_cases')}</p>
            ) : (
              <ul style={{ listStyle: 'none', padding: 0, marginTop: 'var(--space-4)' }}>
                {cases.map((c) => (
                  <li key={c.id} className="metric-card" style={{ marginBottom: 'var(--space-2)', cursor: 'pointer' }} onClick={() => { setActive(c); setAssessment(c.assessment_json); setReview(c.ai_review_json ?? null); setReport(null); setStep('assess') }}>
                    <div className="label">{c.question}</div>
                    <div className="meta">{t(`decision.status.${c.status}`)}</div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}

        {active && step === 'assess' && (
          <>
            <h3 style={{ marginTop: 'var(--space-4)' }}>{active.question}</h3>
            {!assessment ? (
              <button className="primary" onClick={() => void runAssess()} disabled={busy}>
                {busy ? t('decision.assessing') : t('decision.assess')}
              </button>
            ) : (
              <>
                <p><strong>{t('decision.risk_level')}：</strong>
                  <span className={RISK_CLASS[assessment.risk_level]}>
                    {t(`decision.risk.${assessment.risk_level}`)}
                  </span>
                </p>
                {assessment.warnings.length > 0 && (
                  <div className="reminder">
                    {assessment.warnings.map((w, i) => <div key={i}>• {w}</div>)}
                  </div>
                )}
                <h4>{t('decision.gaps')}</h4>
                <ul>
                  {assessment.knowledge_gaps.map((g) => (
                    <li key={g.id}>{g.question}</li>
                  ))}
                </ul>
                <h4>{t('decision.criteria')}</h4>
                <ul>
                  {assessment.criteria.map((c) => (
                    <li key={c.key}>{c.name}（权重 {c.default_weight}）</li>
                  ))}
                </ul>
                <AiReviewSection
                  t={t}
                  review={review}
                  reviewing={reviewing}
                  onRun={() => void runAiReview()}
                />
                <button style={{ marginTop: 'var(--space-2)' }} onClick={() => void runAssess()} disabled={busy}>
                  {t('decision.reassess')}
                </button>
                <p className="muted" style={{ marginTop: 'var(--space-4)' }}>{t('decision.pack_optional_hint')}</p>
                <button onClick={() => setStep('pack')} disabled={assessment.skip_research}>
                  {t('decision.gen_pack')}
                </button>
              </>
            )}
          </>
        )}

        {active && step === 'pack' && (
          <>
            <h3 style={{ marginTop: 'var(--space-4)' }}>{active.question}</h3>
            <p className="muted">{t('decision.pack_optional_hint')}</p>
            {tasks.length === 0 ? (
              <button className="primary" onClick={() => void generatePack()} disabled={busy}>
                {t('decision.gen_pack')}
              </button>
            ) : (
              <>
                <p>{t('decision.pack_generated')}</p>
                {tasks.map((task) => (
                  <div key={task.id} className="card" style={{ marginTop: 'var(--space-3)' }}>
                    {gapLabel(task.question_key) && (
                      <div className="label">{gapLabel(task.question_key)}</div>
                    )}
                    <pre style={{ whiteSpace: 'pre-wrap', fontSize: 'var(--font-sm)' }}>{task.prompt_text}</pre>
                    <button onClick={() => { navigator.clipboard.writeText(task.prompt_text); onNotice(t('decision.copied')) }}>
                      {t('decision.copy_prompt')}
                    </button>
                  </div>
                ))}
                <button className="primary" style={{ marginTop: 'var(--space-4)' }} onClick={() => setStep('paste')}>
                  {t('decision.to_paste')}
                </button>
              </>
            )}
          </>
        )}

        {active && step === 'paste' && (
          <>
            <h3 style={{ marginTop: 'var(--space-4)' }}>{active.question}</h3>
            {tasks.map((task) => (
              <div key={task.id} className="card" style={{ marginTop: 'var(--space-3)' }}>
                {gapLabel(task.question_key) && (
                  <div className="label">{gapLabel(task.question_key)}</div>
                )}
                <textarea
                  value={pastingTask === task.id ? pasteText : ''}
                  onChange={(e) => { if (pastingTask === task.id) setPasteText(e.target.value); else { setPastingTask(task.id); setPasteText(e.target.value) } }}
                  placeholder={t('decision.paste_hint')}
                  rows={4}
                  style={{ width: '100%' }}
                />
                <button onClick={() => void doPaste(task.id)} disabled={pastingTask === task.id && busy}>
                  {t('decision.import')}
                </button>
              </div>
            ))}

            {evidence.length > 0 && (
              <>
                <h4 style={{ marginTop: 'var(--space-4)' }}>{t('decision.evidence')}</h4>
                {evidence.map((ev) => (
                  <div key={ev.id} className="metric-card" style={{ marginBottom: 'var(--space-2)' }}>
                    <div className="label">{ev.claim}</div>
                    <div className="meta">
                      {ev.data_value && `${ev.data_value} `}
                      {ev.source_name && `${ev.source_name} `}
                      <span className={ev.user_verified ? '' : 'delta down'}>
                        {ev.user_verified ? t('decision.verified') : t('decision.unverified')}
                      </span>
                      {ev.stale && ` · ${t('decision.stale')}`}
                      {ev.conflicts_with && ` · ${t('decision.conflict')}`}
                      {' · '}{sourceLabel(ev.source_type)}{' / '}{confidenceLabel(ev.confidence)}
                    </div>
                    <button onClick={() => void toggleVerify(ev)}>
                      {ev.user_verified ? t('decision.mark_unverified') : t('decision.mark_verified')}
                    </button>
                  </div>
                ))}
              </>
            )}

            <AiReviewSection
              t={t}
              review={review}
              reviewing={reviewing}
              onRun={() => void runAiReview()}
            />

            <button className="primary" style={{ marginTop: 'var(--space-4)' }} onClick={() => void synthesize()} disabled={busy || evidence.length === 0}>
              {busy ? t('decision.synthesizing') : t('decision.synthesize')}
            </button>
          </>
        )}

        {active && step === 'synthesize' && report && (
          <>
            <DecisionReportView report={report} t={t} />
            {review && <AiReviewView review={review} t={t} />}
          </>
        )}
      </div>
    </div>
  )
}

function DecisionReportView({ report, t }: { report: Record<string, unknown>; t: (k: string) => string }) {
  const eq = report.evidence_quality as { total: number; unverified: number; stale: number } | undefined
  const highRiskNotice = report.high_risk_notice as string | undefined
  const downweightNote = report.downweight_note as string
  const assumptions = report.key_assumptions as string[]
  const missing = report.missing_information as string[]
  return (
    <div style={{ marginTop: 'var(--space-4)' }}>
      <h3>{t('decision.evidence_quality')}</h3>
      {eq && (
        <p>共 {eq.total} 条；未核实 {eq.unverified} 条；过期 {eq.stale} 条</p>
      )}
      {highRiskNotice && (
        <div className="reminder">{highRiskNotice}</div>
      )}
      <p className="muted">{downweightNote}</p>

      <h4>{t('decision.key_assumptions')}</h4>
      <ul>
        {assumptions.map((a, i) => <li key={i}>{a}</li>)}
      </ul>

      <h4>{t('decision.missing')}</h4>
      <ul>
        {missing.map((m, i) => <li key={i}>{m}</li>)}
      </ul>
    </div>
  )
}

/** AI 解读入口：评估完成即可点，不要求先导入外部证据（第 10.6 节）。 */
function AiReviewSection({
  t,
  review,
  reviewing,
  onRun,
}: {
  t: (k: string) => string
  review: AiReview | null
  reviewing: boolean
  onRun: () => void
}) {
  return (
    <div style={{ marginTop: 'var(--space-4)' }}>
      <div className="row wrap" style={{ alignItems: 'baseline' }}>
        <button className="primary" onClick={onRun} disabled={reviewing}>
          {reviewing ? t('decision.ai_reviewing') : t('decision.ai_review')}
        </button>
        <span className="muted">{t('decision.ai_review_hint')}</span>
      </div>
      {review && <AiReviewView review={review} t={t} />}
    </div>
  )
}

/** AI 解读结果：ok 展示模型结论；degraded 展示本地回退与原因（第 10.6 节）。 */
function AiReviewView({ review, t }: { review: AiReview; t: (k: string) => string }) {
  const heading = (
    <div className="row wrap" style={{ alignItems: 'baseline' }}>
      <h3 style={{ marginTop: 'var(--space-4)' }}>{t('decision.ai_review')}</h3>
      {review.model && (
        <span className="muted">{t('decision.ai_model')}：{review.model}</span>
      )}
    </div>
  )

  if (review.status !== 'ok' || !review.analysis) {
    return (
      <div style={{ marginTop: 'var(--space-4)' }}>
        {heading}
        <div className="reminder">
          {review.reason ? renderMessage(review.reason) : t('decision.ai_degraded')}
        </div>
        <p className="muted">{t('decision.ai_fallback_hint')}</p>
        {review.fallback && (
          <DecisionReportView
            report={review.fallback as unknown as Record<string, unknown>}
            t={t}
          />
        )}
      </div>
    )
  }

  const analysis = review.analysis
  const sections: Array<[string, ReportFinding[] | undefined]> = [
    ['decision.ai_summary', analysis.summary],
    ['decision.ai_support', analysis.support],
    ['decision.ai_oppose', analysis.oppose],
    ['decision.ai_assumptions', analysis.assumptions],
    ['decision.ai_risks', analysis.risks],
  ]
  const hasContent =
    sections.some(([, items]) => items && items.length > 0) ||
    (analysis.next_steps && analysis.next_steps.length > 0)

  return (
    <div style={{ marginTop: 'var(--space-4)' }}>
      {heading}
      {!hasContent && <p className="muted">{t('decision.ai_empty')}</p>}
      {sections.map(([key, items]) =>
        items && items.length > 0 ? (
          <div key={key}>
            <h4>{t(key)}</h4>
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {items.map((finding, index) => (
                <li key={index} className="metric-card" style={{ marginBottom: 'var(--space-2)' }}>
                  <div className="label">{finding.rendered_claim ?? finding.claim}</div>
                  {finding.strength && (
                    <div className="meta">
                      <span className={`tag strength strength-${finding.strength}`}>
                        {strengthLabel(finding.strength)}
                      </span>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null,
      )}
      {analysis.next_steps && analysis.next_steps.length > 0 && (
        <div>
          <h4>{t('decision.ai_next_steps')}</h4>
          <ul>
            {analysis.next_steps.map((step, index) => <li key={index}>{step}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}
