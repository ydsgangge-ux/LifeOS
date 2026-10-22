/** 系统判断回顾（第 16.6 节、第 11.1 节「系统判断回顾」页）。
 *
 * 只做一件事：把过去所有判断的落点如实统计出来，不只展示说中的那些——
 * 成立、被推翻、无法判断、用户驳回各占多少一目了然，避免选择性记忆。
 * 列表按时间倒序，每条判断带它当时基于的陈述与回查时间。
 */

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import { judgmentOutcomeLabel } from '../lib/labels'
import type { JudgmentReview as JudgmentReviewData } from '../lib/types'

/** 展示顺序：先待回查，再看落点（第 16.6 节）。 */
const OUTCOMES = ['pending', 'held', 'failed', 'inconclusive', 'user_overrode'] as const

interface Props {
  onError: (problem: unknown, fallback: string) => void
}

function shortTime(iso: string | null): string {
  return iso ? iso.slice(0, 16).replace('T', ' ') : '—'
}

export function JudgmentReview({ onError }: Props) {
  const { t } = useTranslation()
  const [data, setData] = useState<JudgmentReviewData | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.getJudgments())
    } catch (problem) {
      onError(problem, t('error.load_entries'))
    } finally {
      setLoading(false)
    }
  }, [onError, t])

  useEffect(() => {
    void load()
  }, [load])

  if (loading && !data) {
    return (
      <div className="content single">
        <p className="muted">{t('entry.loading')}</p>
      </div>
    )
  }

  const stats = data?.stats
  const items = data?.items ?? []

  return (
    <div className="content single">
      <div className="card">
        <h2>{t('selfcheck.judgments_title')}</h2>
        <p className="muted">{t('selfcheck.judgments_hint')}</p>
        {stats && stats.total > 0 ? (
          <>
            <div className="muted" style={{ marginTop: 'var(--space-2)' }}>
              {t('selfcheck.judgment_total', { total: stats.total })}
            </div>
            <div className="lineage">
              {OUTCOMES.map((outcome) => (
                <div key={outcome}>
                  {t('selfcheck.judgment_row', {
                    outcome: judgmentOutcomeLabel(outcome),
                    count: stats.counts[outcome] ?? 0,
                    share: `${Math.round((stats.shares[outcome] ?? 0) * 100)}%`,
                  })}
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="muted">{t('selfcheck.judgments_empty')}</p>
        )}
      </div>

      {items.length > 0 && (
        <div className="card">
          {items.map((item) => (
            <div className="finding" key={item.id}>
              <div className="finding-head">
                <span className="finding-claim">{item.statement}</span>
                <span className="tag">{judgmentOutcomeLabel(item.outcome)}</span>
              </div>
              <div className="muted">
                {t('selfcheck.recorded')}：{shortTime(item.made_at)} ·{' '}
                {item.check_after ? shortTime(item.check_after) : '—'}
              </div>
              {item.outcome_note && <div className="muted">{item.outcome_note}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
