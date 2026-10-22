/** 计划与目标页（第 11.1 节、第 8.6 节）：目标列表 + 计划状态 + 诊断徽标 + 目标回看。
 *
 * 计划诊断由后端算（GET /plans/{id}/diagnosis），前端只展示判定与理由：
 * 判定用 report.verdict.* 词条，理由是后端给的 {key, params} 消息对象，本地化后显示
 * （NFR-08）。回看写 goal_checkpoint（POST /goals/{id}/checkpoints）。
 */

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import {
  cadenceLabel,
  decisionLabel,
  directionLabel,
  goalStatusLabel,
  horizonLabel,
  planStatusLabel,
  renderMessage,
  verdictLabel,
} from '../lib/labels'
import type { Goal, PlanDiagnosis, Plan } from '../lib/types'

const HORIZONS = ['1y', '3y', '5y', 'life']
const DIRECTIONS = ['increase', 'decrease', 'maintain']
const CADENCES = ['daily', 'weekly', 'monthly', 'once']
const DECISIONS = ['', 'keep', 'revise', 'pause', 'drop', 'achieve']

interface Props {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}

export function GoalsPlans({ onError, onNotice }: Props) {
  const { t } = useTranslation()
  const [goals, setGoals] = useState<Goal[]>([])
  const [plans, setPlans] = useState<Plan[]>([])
  const [diagnoses, setDiagnoses] = useState<Record<string, PlanDiagnosis | null>>({})
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [showGoalForm, setShowGoalForm] = useState(false)
  const [showPlanForm, setShowPlanForm] = useState(false)
  const [checkpointFor, setCheckpointFor] = useState<string | null>(null)

  const [goalDraft, setGoalDraft] = useState({
    title: '',
    horizon: '1y',
    direction: 'increase',
    target: '',
    target_date: '',
    why_note: '',
  })
  const [planDraft, setPlanDraft] = useState({
    title: '',
    goal_id: '',
    cadence: 'weekly',
    expected_output: '',
  })
  const [checkpointDraft, setCheckpointDraft] = useState({
    metric_value: '',
    confidence: '3',
    decision: '',
    progress_note: '',
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [goalList, planList] = await Promise.all([api.listGoals(), api.listPlans()])
      setGoals(goalList.items)
      setPlans(planList.items)
      const entries = await Promise.all(
        planList.items.map(async (plan) => {
          try {
            return [plan.id, await api.planDiagnosis(plan.id)] as const
          } catch {
            return [plan.id, null] as const
          }
        }),
      )
      setDiagnoses(Object.fromEntries(entries))
    } catch (problem) {
      onError(problem, t('error.load_forms'))
    } finally {
      setLoading(false)
    }
  }, [onError, t])

  useEffect(() => {
    void load()
  }, [load])

  const handleCreateGoal = async () => {
    if (!goalDraft.title.trim()) return
    setSubmitting(true)
    try {
      await api.createGoal({
        title: goalDraft.title.trim(),
        horizon: goalDraft.horizon,
        direction: goalDraft.direction,
        target: goalDraft.target === '' ? null : Number(goalDraft.target),
        target_date: goalDraft.target_date || null,
        why_note: goalDraft.why_note || null,
      })
      setGoalDraft({ title: '', horizon: '1y', direction: 'increase', target: '', target_date: '', why_note: '' })
      setShowGoalForm(false)
      await load()
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setSubmitting(false)
    }
  }

  const handleCreatePlan = async () => {
    if (!planDraft.title.trim()) return
    setSubmitting(true)
    try {
      await api.createPlan({
        title: planDraft.title.trim(),
        goal_id: planDraft.goal_id || null,
        cadence: planDraft.cadence,
        expected_output: planDraft.expected_output || null,
        status: 'active',
      })
      setPlanDraft({ title: '', goal_id: '', cadence: 'weekly', expected_output: '' })
      setShowPlanForm(false)
      await load()
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setSubmitting(false)
    }
  }

  const handleCheckpoint = async (goalId: string) => {
    setSubmitting(true)
    try {
      await api.addGoalCheckpoint(goalId, {
        metric_value: checkpointDraft.metric_value === '' ? null : Number(checkpointDraft.metric_value),
        confidence: Number(checkpointDraft.confidence),
        decision: checkpointDraft.decision || null,
        progress_note: checkpointDraft.progress_note || null,
      })
      setCheckpointDraft({ metric_value: '', confidence: '3', decision: '', progress_note: '' })
      setCheckpointFor(null)
      onNotice(t('goals.checkpoint_saved'))
      await load()
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setSubmitting(false)
    }
  }

  const goalTitle = (goalId: string | null) =>
    goals.find((goal) => goal.id === goalId)?.title ?? ''

  return (
    <div className="content">
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2>{t('goals.title')}</h2>
          <button onClick={() => setShowGoalForm((v) => !v)}>{t('goals.new_goal')}</button>
        </div>

        {showGoalForm && (
          <div className="field-block">
            <div className="field">
              <label>{t('goals.title_label')}</label>
              <input
                value={goalDraft.title}
                onChange={(e) => setGoalDraft({ ...goalDraft, title: e.target.value })}
              />
            </div>
            <div className="grid-2">
              <div>
                <label>{t('goals.horizon')}</label>
                <select
                  value={goalDraft.horizon}
                  onChange={(e) => setGoalDraft({ ...goalDraft, horizon: e.target.value })}
                >
                  {HORIZONS.map((h) => (
                    <option key={h} value={h}>
                      {horizonLabel(h)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label>{t('goals.direction')}</label>
                <select
                  value={goalDraft.direction}
                  onChange={(e) => setGoalDraft({ ...goalDraft, direction: e.target.value })}
                >
                  {DIRECTIONS.map((d) => (
                    <option key={d} value={d}>
                      {directionLabel(d)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label>{t('goals.target')}</label>
                <input
                  type="number"
                  value={goalDraft.target}
                  onChange={(e) => setGoalDraft({ ...goalDraft, target: e.target.value })}
                />
              </div>
              <div>
                <label>{t('goals.target_date')}</label>
                <input
                  type="date"
                  value={goalDraft.target_date}
                  onChange={(e) => setGoalDraft({ ...goalDraft, target_date: e.target.value })}
                />
              </div>
            </div>
            <div className="field">
              <label>{t('goals.why')}</label>
              <textarea
                value={goalDraft.why_note}
                onChange={(e) => setGoalDraft({ ...goalDraft, why_note: e.target.value })}
              />
            </div>
            <div className="row">
              <button className="primary" disabled={submitting} onClick={() => void handleCreateGoal()}>
                {t('goals.create')}
              </button>
              <button className="ghost" onClick={() => setShowGoalForm(false)}>
                {t('goals.cancel')}
              </button>
            </div>
          </div>
        )}

        {loading && <p className="muted">{t('entry.loading')}</p>}
        {!loading && goals.length === 0 && <p className="muted">{t('goals.empty')}</p>}

        {goals.map((goal) => (
          <div className="entry" key={goal.id}>
            <div className="head">
              <span className="title">{goal.title}</span>
              <span className="tag">{goalStatusLabel(goal.status)}</span>
            </div>
            <div style={{ marginTop: 'var(--space-1)' }}>
              {goal.horizon && <span className="tag">{horizonLabel(goal.horizon)}</span>}
              {goal.direction && <span className="tag">{directionLabel(goal.direction)}</span>}
              {goal.target !== null && (
                <span className="tag domain">
                  {goal.target} {goal.metric_unit ?? ''}
                </span>
              )}
              {goal.target_date && <span className="tag">{goal.target_date}</span>}
            </div>
            {goal.why_note && <div className="detail">{goal.why_note}</div>}

            {checkpointFor === goal.id ? (
              <div className="field-block">
                <div className="grid-2">
                  <div>
                    <label>{t('goals.checkpoint_value')}</label>
                    <input
                      type="number"
                      value={checkpointDraft.metric_value}
                      onChange={(e) =>
                        setCheckpointDraft({ ...checkpointDraft, metric_value: e.target.value })
                      }
                    />
                  </div>
                  <div>
                    <label>{t('goals.checkpoint_confidence')}</label>
                    <input
                      type="number"
                      min={1}
                      max={5}
                      value={checkpointDraft.confidence}
                      onChange={(e) =>
                        setCheckpointDraft({ ...checkpointDraft, confidence: e.target.value })
                      }
                    />
                  </div>
                </div>
                <div className="field">
                  <label>{t('goals.checkpoint_decision')}</label>
                  <select
                    value={checkpointDraft.decision}
                    onChange={(e) =>
                      setCheckpointDraft({ ...checkpointDraft, decision: e.target.value })
                    }
                  >
                    {DECISIONS.map((d) => (
                      <option key={d || 'none'} value={d}>
                        {d ? decisionLabel(d) : t('goals.checkpoint_none')}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label>{t('goals.checkpoint_note')}</label>
                  <input
                    value={checkpointDraft.progress_note}
                    onChange={(e) =>
                      setCheckpointDraft({ ...checkpointDraft, progress_note: e.target.value })
                    }
                  />
                </div>
                <div className="row">
                  <button
                    className="primary"
                    disabled={submitting}
                    onClick={() => void handleCheckpoint(goal.id)}
                  >
                    {t('goals.checkpoint')}
                  </button>
                  <button className="ghost" onClick={() => setCheckpointFor(null)}>
                    {t('goals.cancel')}
                  </button>
                </div>
              </div>
            ) : (
              <div className="row" style={{ marginTop: 'var(--space-2)' }}>
                <button className="ghost" onClick={() => setCheckpointFor(goal.id)}>
                  {t('goals.checkpoint')}
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2>{t('goals.plans_title')}</h2>
          <button onClick={() => setShowPlanForm((v) => !v)}>{t('goals.new_plan')}</button>
        </div>

        {showPlanForm && (
          <div className="field-block">
            <div className="field">
              <label>{t('goals.title_label')}</label>
              <input
                value={planDraft.title}
                onChange={(e) => setPlanDraft({ ...planDraft, title: e.target.value })}
              />
            </div>
            <div className="grid-2">
              <div>
                <label>{t('goals.plan_goal')}</label>
                <select
                  value={planDraft.goal_id}
                  onChange={(e) => setPlanDraft({ ...planDraft, goal_id: e.target.value })}
                >
                  <option value="">{t('goals.plan_none')}</option>
                  {goals.map((goal) => (
                    <option key={goal.id} value={goal.id}>
                      {goal.title}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label>{t('goals.plan_cadence')}</label>
                <select
                  value={planDraft.cadence}
                  onChange={(e) => setPlanDraft({ ...planDraft, cadence: e.target.value })}
                >
                  {CADENCES.map((c) => (
                    <option key={c} value={c}>
                      {cadenceLabel(c)}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="field">
              <label>{t('goals.plan_expected')}</label>
              <input
                value={planDraft.expected_output}
                onChange={(e) => setPlanDraft({ ...planDraft, expected_output: e.target.value })}
              />
            </div>
            <div className="row">
              <button className="primary" disabled={submitting} onClick={() => void handleCreatePlan()}>
                {t('goals.create')}
              </button>
              <button className="ghost" onClick={() => setShowPlanForm(false)}>
                {t('goals.cancel')}
              </button>
            </div>
          </div>
        )}

        {!loading && plans.length === 0 && <p className="muted">{t('goals.plans_empty')}</p>}

        {plans.map((plan) => {
          const diagnosis = diagnoses[plan.id]
          return (
            <div className="entry" key={plan.id}>
              <div className="head">
                <span className="title">{plan.title}</span>
                <span className="tag">{planStatusLabel(plan.status)}</span>
              </div>
              <div style={{ marginTop: 'var(--space-1)' }}>
                {plan.cadence && <span className="tag">{cadenceLabel(plan.cadence)}</span>}
                {plan.goal_id && <span className="tag domain">{goalTitle(plan.goal_id)}</span>}
                {diagnosis ? (
                  <span className={`tag verdict verdict-${diagnosis.verdict}`}>
                    {t('goals.diagnosis')}：{verdictLabel(diagnosis.verdict)}
                  </span>
                ) : (
                  <span className="tag">{t('goals.diagnosis_loading')}</span>
                )}
              </div>
              {plan.expected_output && <div className="detail">{plan.expected_output}</div>}
              {diagnosis && diagnosis.reasons.length > 0 && (
                <div className="detail">
                  {diagnosis.reasons.map((reason, index) => (
                    <div key={index}>{renderMessage(reason)}</div>
                  ))}
                </div>
              )}
            </div>
          )
        })}

        <p className="muted" style={{ marginTop: 'var(--space-3)' }}>
          {t('goals.diagnosis_excluded_hint')}
        </p>
      </div>
    </div>
  )
}
