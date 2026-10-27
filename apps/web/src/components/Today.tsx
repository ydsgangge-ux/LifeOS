/** 今日页（第 11.1 节）：一屏完成当天事项 —— 快速录入、计划打卡、提醒。
 *
 * 提醒全部由本地已有数据推导（今日条目数、目标到期日、计划最近打卡），
 * 不做额外的服务端接口；数据量固定在一个小窗口内，节点数是常数级。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import { logicalDate, logicalToday } from '../lib/day'
import { formatDate } from '../lib/format'
import { formTitle } from '../lib/labels'
import type { FormDef, Plan } from '../lib/types'
import { FormRenderer } from './FormRenderer'

const PLAN_WINDOW_DAYS = 60
const STALE_DAYS = 7
const REVIEW_WINDOW_DAYS = 30

function daysSince(value: string): number {
  const then = new Date(`${value.slice(0, 10)}T00:00:00`)
  // 与后端一致：凌晨 0–4 点仍算前一天，否则「多久没打卡」会多算一天。
  const now = logicalToday()
  return Math.round((now.getTime() - then.getTime()) / 86_400_000)
}

interface Props {
  ageBand: string | null
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}

export function Today({ ageBand, onError, onNotice }: Props) {
  const { t } = useTranslation()
  const [forms, setForms] = useState<FormDef[]>([])
  const [activeFormId, setActiveFormId] = useState('')
  const [plans, setPlans] = useState<Plan[]>([])
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [todayCount, setTodayCount] = useState<number | null>(null)
  const [lastCheckIn, setLastCheckIn] = useState<Record<string, string>>({})
  const [goalDue, setGoalDue] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const today = logicalDate()

  const activeForm = useMemo(
    () => forms.find((form) => form.id === activeFormId) ?? null,
    [forms, activeFormId],
  )

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [formList, planList, todayPage, planEntries, goalList] = await Promise.all([
        api.listForms(ageBand ?? undefined),
        api.listPlans({ status: 'active' }),
        api.listEntries({ date_from: today, date_to: today, limit: 1 }),
        api.listEntries({ domain: 'plan', date_from: logicalDate(PLAN_WINDOW_DAYS), limit: 200 }),
        api.listGoals('active'),
      ])
      setForms(formList.items)
      setActiveFormId((previous) => previous || (formList.items[0]?.id ?? ''))
      setPlans(planList.items)
      setTodayCount(todayPage.items.length)

      const latest: Record<string, string> = {}
      for (const entry of planEntries.items) {
        const planId = entry.detail.plan_id
        if (typeof planId !== 'string') continue
        if (!latest[planId] || entry.occurred_date > latest[planId]) {
          latest[planId] = entry.occurred_date
        }
      }
      setLastCheckIn(latest)
      // 今天已经打过卡的计划直接显示「已打卡」，刷新后也不会又冒出打卡按钮
      setChecked(
        Object.fromEntries(
          Object.entries(latest)
            .filter(([, date]) => date === today)
            .map(([planId]) => [planId, true]),
        ),
      )

      setGoalDue(
        goalList.items
          .filter((goal) => {
            if (!goal.target_date) return false
            const target = goal.target_date.slice(0, 10)
            // 到期日落在「今天 → 未来 30 天」之间的目标，提醒回看（第 8.6 节）。
            return target >= today && target <= logicalDate(-REVIEW_WINDOW_DAYS)
          })
          .map((goal) => goal.title),
      )
    } catch (problem) {
      onError(problem, t('error.load_entries'))
    } finally {
      setLoading(false)
    }
  }, [ageBand, onError, t, today])

  useEffect(() => {
    void load()
  }, [load])

  const submitQuick = async (values: Record<string, unknown>) => {
    if (!activeForm) return
    setSubmitting(true)
    try {
      await api.submitForm(activeForm.id, values)
      onNotice(t('today.checked'))
      setTodayCount((previous) => (previous ?? 0) + 1)
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setSubmitting(false)
    }
  }

  const checkIn = async (plan: Plan, outcome: 'done' | 'skipped') => {
    setSubmitting(true)
    try {
      await api.addPlanRecord(plan.id, { outcome, status: 'final' })
      setChecked((previous) => ({ ...previous, [plan.id]: true }))
      setLastCheckIn((previous) => ({ ...previous, [plan.id]: today }))
      onNotice(t('today.checked'))
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setSubmitting(false)
    }
  }

  const stalePlans = plans.filter((plan) => {
    const reference = plan.start_date ?? plan.created_at.slice(0, 10)
    if (daysSince(reference) < STALE_DAYS) return false
    const last = lastCheckIn[plan.id]
    return !last || daysSince(last) >= STALE_DAYS
  })

  const reminders: string[] = []
  if (todayCount === 0) reminders.push(t('today.reminder.no_records'))
  for (const title of goalDue) reminders.push(t('today.reminder.review_goal', { title }))
  for (const plan of stalePlans) reminders.push(t('today.reminder.plan_stale', { title: plan.title }))

  return (
    <div className="content">
      <div className="card">
        <h2>{t('today.title')}</h2>
        <p className="muted">{t('today.date', { date: formatDate(today) })}</p>
      </div>

      <div className="card">
        <h3>{t('today.quick_title')}</h3>
        <p className="muted">{t('today.quick_hint')}</p>
        {forms.length === 0 && !loading && <p className="muted">{t('today.quick_empty')}</p>}
        <div className="row wrap today-quick">
          {forms.map((form) => (
            <button
              key={form.id}
              className={form.id === activeFormId ? 'primary' : ''}
              onClick={() => setActiveFormId(form.id)}
            >
              {formTitle(form.title)}
            </button>
          ))}
        </div>
        {activeForm && (
          <div style={{ marginTop: 'var(--space-3)' }}>
            <FormRenderer
              key={activeForm.id}
              form={activeForm}
              ageBand={ageBand ?? undefined}
              submitting={submitting}
              onSubmit={(values) => void submitQuick(values)}
            />
          </div>
        )}
      </div>

      <div className="card">
        <h3>{t('today.plans_title')}</h3>
        <p className="muted">{t('today.plans_hint')}</p>
        {loading && <p className="muted">{t('entry.loading')}</p>}
        {!loading && plans.length === 0 && <p className="muted">{t('today.plans_empty')}</p>}
        {plans.map((plan) => (
          <div className="plan-check" key={plan.id}>
            <span className="title">{plan.title}</span>
            {checked[plan.id] ? (
              <span className="muted">{t('today.check_in')}</span>
            ) : (
              <div className="row">
                <button disabled={submitting} onClick={() => void checkIn(plan, 'done')}>
                  {t('today.check_in_do')}
                </button>
                <button
                  className="ghost"
                  disabled={submitting}
                  onClick={() => void checkIn(plan, 'skipped')}
                >
                  {t('today.check_in_skipped')}
                </button>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="card">
        <h3>{t('today.reminders_title')}</h3>
        {reminders.length === 0 && <p className="muted">{t('today.reminders_empty')}</p>}
        {reminders.map((text) => (
          <div className="reminder" key={text}>
            {text}
          </div>
        ))}
      </div>
    </div>
  )
}
