/** 出站预览与策略设置（第 7.4 节、第 11.1 节）。
 *
 * 三块内容：切换出站策略（儿童/青少年档固定 none，不可改）、按用途预览将要出站的
 * 内容（本地计算，不发送）、查看出站审计记录（只存哈希与字节数，不存内容）。
 */

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import { ageBandLabel, egressLogStatusLabel, policyLabel, renderMessage } from '../lib/labels'
import type { EgressLogItem, EgressPolicyInfo, EgressPreview } from '../lib/types'

const PURPOSES = [
  { purpose: 'weekly_report', period: 'weekly' },
  { purpose: 'monthly_report', period: 'monthly' },
] as const

interface Props {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}

export function EgressSettings({ onError, onNotice }: Props) {
  const { t } = useTranslation()
  const [info, setInfo] = useState<EgressPolicyInfo | null>(null)
  const [log, setLog] = useState<EgressLogItem[]>([])
  const [draftPolicy, setDraftPolicy] = useState('')
  const [loading, setLoading] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [preview, setPreview] = useState<EgressPreview | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [policy, logPage] = await Promise.all([
        api.getEgressPolicy(),
        api.listEgressLog(),
      ])
      setInfo(policy)
      setDraftPolicy(policy.policy)
      setLog(logPage.items)
    } catch (problem) {
      onError(problem, t('error.load_entries'))
    } finally {
      setLoading(false)
    }
  }, [onError, t])

  useEffect(() => {
    void load()
  }, [load])

  const savePolicy = async () => {
    if (!info || draftPolicy === info.policy) return
    try {
      await api.setEgressPolicy(draftPolicy)
      onNotice(t('egress.saved'))
      await load()
    } catch (problem) {
      onError(problem, t('error.save'))
    }
  }

  const runPreview = async (purpose: string, period: string) => {
    setPreviewing(true)
    try {
      setPreview(await api.previewEgress({ purpose, period }))
    } catch (problem) {
      onError(problem, t('error.load_entries'))
    } finally {
      setPreviewing(false)
    }
  }

  return (
    <div className="content">
      <div className="card">
        <h2>{t('egress.title')}</h2>
        {loading && !info && <p className="muted">{t('entry.loading')}</p>}
        {info && (
          <>
            <p className="muted">
              {t('egress.current')}：{policyLabel(info.policy)} ·{' '}
              {t('egress.age_band', { band: ageBandLabel(info.age_band) })}
            </p>
            {info.locked && <div className="notice warn">{t('egress.policy_locked')}</div>}
            <div className="field-block">
              {info.choices.map((choice) => (
                <label key={choice} className="row" style={{ cursor: info.locked ? 'default' : 'pointer' }}>
                  <input
                    type="radio"
                    name="egress-policy"
                    style={{ width: 'auto' }}
                    disabled={info.locked}
                    checked={draftPolicy === choice}
                    onChange={() => setDraftPolicy(choice)}
                  />
                  <span>{policyLabel(choice)}</span>
                </label>
              ))}
            </div>
            <div className="row">
              <button
                className="primary"
                disabled={info.locked || draftPolicy === info.policy}
                onClick={() => void savePolicy()}
              >
                {t('egress.save')}
              </button>
            </div>
          </>
        )}

        <h3>{t('egress.log_title')}</h3>
        {log.length === 0 && <p className="muted">{t('egress.log_empty')}</p>}
        {log.map((item) => (
          <div key={item.id} className="muted">
            {t('egress.log_row', {
              ts: item.ts.slice(0, 16).replace('T', ' '),
              purpose: item.purpose,
              status: egressLogStatusLabel(item.status),
              bytes: item.payload_bytes ?? 0,
            })}
          </div>
        ))}
      </div>

      <div className="card">
        <h2>{t('egress.preview')}</h2>
        <p className="muted">{t('egress.preview_hint')}</p>
        <div className="row wrap">
          {PURPOSES.map((item) => (
            <button
              key={item.purpose}
              disabled={previewing}
              onClick={() => void runPreview(item.purpose, item.period)}
            >
              {item.period === 'weekly' ? t('reports.kind.weekly') : t('reports.kind.monthly')}
            </button>
          ))}
        </div>

        {preview && (
          <div style={{ marginTop: 'var(--space-3)' }}>
            <div className={`notice ${preview.allowed ? 'ok' : 'warn'}`}>
              {preview.allowed ? t('egress.allowed') : t('egress.blocked')} ·{' '}
              {renderMessage(preview.reason)}
            </div>
            {preview.report.dropped_count > 0 && (
              <p className="muted">
                {t('egress.dropped', { count: preview.report.dropped_count })}
                {' · '}
                {t('egress.dropped_list', { paths: preview.report.dropped.join('、') })}
              </p>
            )}
            <p className="muted">
              {t('egress.transforms', {
                money: preview.report.transforms.money_to_ratio ?? 0,
                money_dropped: preview.report.transforms.money_dropped ?? 0,
                week: preview.report.transforms.date_to_week ?? 0,
              })}
            </p>
            <h3>{t('egress.payload')}</h3>
            <pre className="payload">{JSON.stringify(preview.payload, null, 2)}</pre>
          </div>
        )}
      </div>
    </div>
  )
}
