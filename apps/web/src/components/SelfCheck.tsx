/** 自我认知（第 16 章、第 11.1 节「自我认知」页）。
 *
 * 三块内容：
 *   - 设置：开关、暂停、每周挑战性问题上限（第 16.8 / 16.9 节）。儿童/青少年档
 *     整个模块不提供，这里只显示一句说明。
 *   - 我的自述：写下一句对自己的判断，选类型后「开始比对」。数据不够时只列缺口，
 *     不生成挑战性问题（第 16.4 / 16.6 节）。
 *   - 系统判断：检测器给出的可检验陈述，如实分栏——记录显示的是事实，可能的其他
 *     解释至少两条且至少一条与「自述是对的」相容，另有「什么会推翻它」和数据覆盖。
 *     用户可接受、驳回或补充背景；驳回后 4 周内不再重复提出（由后端拦截）。
 *
 * 事实/推测分栏与证据血缘都来自后端结构，这里只做本地化与呈现，不自己下结论。
 */

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import {
  claimKindLabel,
  claimStatusLabel,
  hypothesisStatusLabel,
  hypothesisStrengthLabel,
  renderMessage,
  selfCheckKeyLabel,
} from '../lib/labels'
import type {
  Hypothesis,
  MessageObject,
  PageReview,
  SelfCheckSettings,
  SelfClaim,
  SelfReviewAnalysis,
} from '../lib/types'
import { PageAiReview } from './PageAiReview'
import { ProfileBasics } from './ProfileBasics'

const KINDS = ['strength', 'difficulty', 'value', 'plan_belief', 'change'] as const
const VALUE_DOMAINS = ['education', 'career', 'hobby']

interface Props {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}

function shortTime(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ')
}

export function SelfCheck({ onError, onNotice }: Props) {
  const { t } = useTranslation()
  const [settings, setSettings] = useState<SelfCheckSettings | null>(null)
  const [claims, setClaims] = useState<SelfClaim[]>([])
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([])
  const [loading, setLoading] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)
  const [generatingId, setGeneratingId] = useState<string | null>(null)
  const [busyHyp, setBusyHyp] = useState<string | null>(null)
  const [confirmRetract, setConfirmRetract] = useState<string | null>(null)
  const [review, setReview] = useState<PageReview<SelfReviewAnalysis> | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const [gaps, setGaps] = useState<Record<string, MessageObject[]>>({})
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [showContext, setShowContext] = useState<Record<string, boolean>>({})

  // 新建自述表单
  const [statement, setStatement] = useState('')
  const [kind, setKind] = useState<string>('strength')
  const [domain, setDomain] = useState('')
  const [whyNote, setWhyNote] = useState('')
  // 生成时的补充参数（部分检测器需要）
  const [skillId, setSkillId] = useState('')
  const [metricKey, setMetricKey] = useState('')
  const [area, setArea] = useState('')
  const [weeklyLimit, setWeeklyLimit] = useState(2)

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const [s, c, h, r] = await Promise.all([
        api.getSelfCheckSettings(),
        api.listClaims(),
        api.listHypotheses(),
        api.getSelfCheckAiReview(),
      ])
      setSettings(s)
      setWeeklyLimit(s.weekly_limit)
      setClaims(c.items)
      setHypotheses(h.items)
      setReview(r.item)
    } catch (problem) {
      onError(problem, t('error.load_entries'))
    } finally {
      setLoading(false)
    }
  }, [onError, t])

  useEffect(() => {
    void loadAll()
  }, [loadAll])

  const saveSettings = async (patch: {
    enabled?: boolean
    paused?: boolean
    weekly_limit?: number
  }) => {
    setSavingSettings(true)
    try {
      const updated = await api.updateSelfCheckSettings(patch)
      setSettings(updated)
      setWeeklyLimit(updated.weekly_limit)
      onNotice(t('selfcheck.saved'))
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setSavingSettings(false)
    }
  }

  const createClaim = async () => {
    if (!statement.trim()) return
    try {
      const claim = await api.createClaim({
        statement: statement.trim(),
        kind,
        domain: domain || null,
        why_note: whyNote.trim() || null,
      })
      setClaims((previous) => [claim, ...previous])
      setStatement('')
      setWhyNote('')
      onNotice(t('notice.saved'))
    } catch (problem) {
      onError(problem, t('error.save'))
    }
  }

  const retractClaim = async (claim: SelfClaim) => {
    try {
      await api.deleteClaim(claim.id)
      setClaims((previous) => previous.filter((item) => item.id !== claim.id))
      setConfirmRetract(null)
      onNotice(t('notice.deleted'))
    } catch (problem) {
      onError(problem, t('error.delete'))
    }
  }

  const generate = async (claim: SelfClaim) => {
    setGeneratingId(claim.id)
    try {
      const out = await api.generateHypotheses(claim.id, {
        skill_id: skillId.trim() || undefined,
        metric_key: metricKey.trim() || undefined,
        area: area.trim() || undefined,
      })
      setGaps((previous) => ({ ...previous, [claim.id]: out.gaps }))
      if (out.blocked) {
        onNotice(renderMessage(out.blocked))
      } else if (out.hypotheses.length > 0) {
        setHypotheses((previous) => [...out.hypotheses, ...previous])
        onNotice(t('notice.saved'))
      } else {
        onNotice(t('selfcheck.gap_only'))
      }
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setGeneratingId(null)
    }
  }

  const runAiReview = async () => {
    setReviewing(true)
    try {
      const result = await api.aiReviewSelfCheck()
      setReview(result)
      onNotice(result.status === 'ok' ? t('selfcheck.ai_done') : t('selfcheck.ai_degraded'))
    } catch (problem) {
      onError(problem, t('selfcheck.ai_failed'))
    } finally {
      setReviewing(false)
    }
  }

  const respond = async (hyp: Hypothesis, action: 'accept' | 'reject' | 'context') => {
    setBusyHyp(hyp.id)
    try {
      const note = notes[hyp.id]?.trim() || undefined
      const updated = await api.respondHypothesis(hyp.id, { action, note })
      setHypotheses((previous) =>
        previous.map((item) => (item.id === hyp.id ? updated : item)),
      )
      const key =
        action === 'accept'
          ? 'selfcheck.accepted'
          : action === 'reject'
            ? 'selfcheck.rejected'
            : 'selfcheck.context_saved'
      onNotice(t(key))
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setBusyHyp(null)
    }
  }

  if (!settings) {
    return (
      <div className="content single">
        <p className="muted">{t('entry.loading')}</p>
      </div>
    )
  }

  // 儿童 / 青少年档：整个模块不提供（第 16.8 节安全阀）
  if (!settings.allowed) {
    return (
      <div className="content single">
        <div className="card">
          <h2>{t('selfcheck.title')}</h2>
          <p className="muted">{t('selfcheck.age_blocked')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="content single">
      <div className="card">
        <h2>{t('selfcheck.title')}</h2>
        <p className="muted">{t('selfcheck.hint')}</p>
      </div>

      <ProfileBasics onError={onError} onNotice={onNotice} />

      {settings.pause && (
        <div className="card">
          <div className="notice warn">{renderMessage(settings.pause)}</div>
        </div>
      )}

      <div className="card">
        <h2>{t('selfcheck.settings_title')}</h2>
        <div className="field-block">
          <label htmlFor="sc-enabled">
            <input
              id="sc-enabled"
              type="checkbox"
              checked={settings.enabled}
              disabled={savingSettings}
              style={{ width: 'auto', marginRight: 'var(--space-2)' }}
              onChange={(event) => void saveSettings({ enabled: event.target.checked })}
            />
            {t('selfcheck.enable')}
          </label>
          <label htmlFor="sc-paused">
            <input
              id="sc-paused"
              type="checkbox"
              checked={settings.paused}
              disabled={savingSettings}
              style={{ width: 'auto', marginRight: 'var(--space-2)' }}
              onChange={(event) => void saveSettings({ paused: event.target.checked })}
            />
            {t('selfcheck.paused')}
          </label>
          <label htmlFor="sc-limit">{t('selfcheck.weekly_limit')}</label>
          <div className="row">
            <input
              id="sc-limit"
              type="number"
              min={0}
              max={7}
              value={weeklyLimit}
              onChange={(event) => setWeeklyLimit(Number(event.target.value))}
            />
            <button
              className="primary"
              disabled={savingSettings}
              onClick={() => void saveSettings({ weekly_limit: weeklyLimit })}
            >
              {t('selfcheck.save')}
            </button>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>{t('selfcheck.claims_title')}</h2>
        <div className="field-block">
          <label htmlFor="sc-statement">{t('selfcheck.claim_statement')}</label>
          <input
            id="sc-statement"
            value={statement}
            onChange={(event) => setStatement(event.target.value)}
          />
          <label htmlFor="sc-kind">{t('selfcheck.claim_kind')}</label>
          <select id="sc-kind" value={kind} onChange={(event) => setKind(event.target.value)}>
            {KINDS.map((item) => (
              <option key={item} value={item}>
                {claimKindLabel(item)}
              </option>
            ))}
          </select>
          <label htmlFor="sc-domain">{t('selfcheck.claim_domain')}</label>
          <select
            id="sc-domain"
            value={domain}
            onChange={(event) => setDomain(event.target.value)}
          >
            <option value="">{t('selfcheck.choose')}</option>
            {VALUE_DOMAINS.map((item) => (
              <option key={item} value={item}>
                {selfCheckKeyLabel(`domain.${item}`)}
              </option>
            ))}
          </select>
          <label htmlFor="sc-why">{t('selfcheck.claim_why')}</label>
          <input
            id="sc-why"
            value={whyNote}
            onChange={(event) => setWhyNote(event.target.value)}
          />
          <div className="row" style={{ marginTop: 'var(--space-3)' }}>
            <button className="primary" disabled={!statement.trim()} onClick={() => void createClaim()}>
              {t('selfcheck.create')}
            </button>
          </div>
        </div>

        <details>
          <summary>{t('selfcheck.extra_params')}</summary>
          <div className="field-block">
            <label htmlFor="sc-skill">{t('selfcheck.extra_skill')}</label>
            <input id="sc-skill" value={skillId} onChange={(e) => setSkillId(e.target.value)} />
            <label htmlFor="sc-metric">{t('selfcheck.extra_metric')}</label>
            <input
              id="sc-metric"
              value={metricKey}
              onChange={(e) => setMetricKey(e.target.value)}
            />
            <label htmlFor="sc-area">{t('selfcheck.extra_area')}</label>
            <input id="sc-area" value={area} onChange={(e) => setArea(e.target.value)} />
          </div>
        </details>

        {loading && claims.length === 0 && <p className="muted">{t('entry.loading')}</p>}
        {!loading && claims.length === 0 && (
          <p className="muted">{t('selfcheck.claim_empty')}</p>
        )}

        {claims.map((claim) => (
          <div className="finding" key={claim.id}>
            <div className="finding-head">
              <span className="finding-claim">{claim.statement}</span>
              <span className="tag">{claimKindLabel(claim.kind)}</span>
              <span className="tag">{claimStatusLabel(claim.status)}</span>
            </div>
            <div className="evidence">
              <button
                className="primary"
                disabled={!settings.enabled || generatingId === claim.id}
                onClick={() => void generate(claim)}
              >
                {generatingId === claim.id ? t('selfcheck.generating') : t('selfcheck.generate')}
              </button>
              {confirmRetract === claim.id ? (
                <>
                  <span className="muted">{t('selfcheck.retract_confirm')}</span>
                  <button className="danger" onClick={() => void retractClaim(claim)}>
                    {t('selfcheck.confirm')}
                  </button>
                  <button className="ghost" onClick={() => setConfirmRetract(null)}>
                    {t('selfcheck.cancel')}
                  </button>
                </>
              ) : (
                <button className="ghost" onClick={() => setConfirmRetract(claim.id)}>
                  {t('selfcheck.retract')}
                </button>
              )}
            </div>
            {(gaps[claim.id] ?? []).length > 0 && (
              <div className="lineage">
                <div className="muted">{t('selfcheck.coverage_missing')}</div>
                {(gaps[claim.id] ?? []).map((gap, index) => (
                  <div key={index}>{renderMessage(gap)}</div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="card">
        <h2>{t('selfcheck.hypotheses_title')}</h2>
        {hypotheses.length === 0 && <p className="muted">{t('selfcheck.hypotheses_empty')}</p>}
        {hypotheses.map((hyp) => (
          <div className="finding" key={hyp.id}>
            <div className="finding-head">
              <span className="finding-claim">{hyp.statement}</span>
              <span className={`tag strength strength-${hyp.strength}`}>
                {t('selfcheck.strength')}: {hypothesisStrengthLabel(hyp.strength)}
              </span>
              <span className="tag">{hypothesisStatusLabel(hyp.status)}</span>
            </div>

            <div className="muted" style={{ marginTop: 'var(--space-2)' }}>
              {t('selfcheck.coverage_days', { days: hyp.coverage.days })}
            </div>

            {hyp.coverage.items.length > 0 && (
              <>
                <div className="section-title">{t('selfcheck.recorded')}</div>
                {hyp.coverage.items.map((item, index) => (
                  <div key={index}>
                    {selfCheckKeyLabel(item.key)}：{String(item.value ?? '—')}
                  </div>
                ))}
              </>
            )}

            <div className="section-title">{t('selfcheck.possible_explanations')}</div>
            {hyp.alternatives.map((alt, index) => (
              <div key={index}>· {alt.text}</div>
            ))}

            <div className="section-title">{t('selfcheck.falsifier')}</div>
            <div>{hyp.falsifier}</div>

            {hyp.evidence.length > 0 && (
              <>
                <div className="section-title">{t('selfcheck.evidence_title')}</div>
                {hyp.evidence.map((ev) => (
                  <div className="evidence" key={ev.id}>
                    <span className="chip static">{ev.kind}</span>
                    <span className="muted">
                      {ev.note ? renderMessage(ev.note) : ev.ref_id}
                    </span>
                  </div>
                ))}
              </>
            )}

            {hyp.coverage.missing.length > 0 && (
              <div className="lineage">
                <div className="muted">{t('selfcheck.coverage_missing')}</div>
                {hyp.coverage.missing.map((gap, index) => (
                  <div key={index}>{renderMessage(gap)}</div>
                ))}
              </div>
            )}

            {hyp.status === 'proposed' && (
              <>
                <div className="evidence">
                  <button
                    className="primary"
                    disabled={busyHyp === hyp.id}
                    onClick={() => void respond(hyp, 'accept')}
                  >
                    {t('selfcheck.accept')}
                  </button>
                  <button
                    className="danger"
                    disabled={busyHyp === hyp.id}
                    onClick={() => void respond(hyp, 'reject')}
                  >
                    {t('selfcheck.reject')}
                  </button>
                  <button
                    className="ghost"
                    onClick={() =>
                      setShowContext((previous) => ({
                        ...previous,
                        [hyp.id]: !previous[hyp.id],
                      }))
                    }
                  >
                    {t('selfcheck.context')}
                  </button>
                </div>
                {showContext[hyp.id] && (
                  <div className="field-block">
                    <textarea
                      value={notes[hyp.id] ?? ''}
                      placeholder={t('selfcheck.context_placeholder')}
                      onChange={(event) =>
                        setNotes((previous) => ({ ...previous, [hyp.id]: event.target.value }))
                      }
                    />
                    <div className="row">
                      <button
                        className="primary"
                        disabled={busyHyp === hyp.id}
                        onClick={() => void respond(hyp, 'context')}
                      >
                        {t('selfcheck.submit')}
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}

            <div className="muted" style={{ marginTop: 'var(--space-2)' }}>
              {t('selfcheck.recorded')}：{shortTime(hyp.proposed_at)}
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <h2>{t('selfcheck.ai_title')}</h2>
        <p className="muted">{t('selfcheck.ai_review_hint')}</p>
        <div className="row wrap" style={{ marginTop: 'var(--space-3)', alignItems: 'baseline' }}>
          <button
            className="primary"
            disabled={reviewing || (claims.length === 0 && hypotheses.length === 0)}
            onClick={() => void runAiReview()}
          >
            {reviewing ? t('selfcheck.ai_reviewing') : t('selfcheck.ai_review')}
          </button>
          {claims.length === 0 && hypotheses.length === 0 && (
            <span className="muted">{t('selfcheck.ai_need_claims')}</span>
          )}
        </div>
        {review && (
          <PageAiReview
            titleKey="selfcheck.ai_result"
            review={review}
            headline={review.analysis?.headline}
            sections={[
              { key: 'selfcheck.ai_observations', items: review.analysis?.observations },
              { key: 'selfcheck.ai_tensions', items: review.analysis?.tensions },
            ]}
            texts={[
              { key: 'selfcheck.ai_questions', items: review.analysis?.questions },
              { key: 'selfcheck.ai_uncertainty', items: review.analysis?.uncertainty },
            ]}
            experiments={[
              { key: 'selfcheck.ai_experiments', items: review.analysis?.experiments },
            ]}
          />
        )}
      </div>
    </div>
  )
}
