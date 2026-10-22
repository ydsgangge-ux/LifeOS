/** 意外区块（第 5.2 / 6.1 节）：表单里的「有意外？」。
 *
 * 意外不是异常处理，而是正常记录的一部分：它单独统计，不因它去丢掉条目。
 * 默认一次性、默认按指标排除（第 5.2 节第 3 条）。
 */

import { useTranslation } from 'react-i18next'

import { anomalyTypeLabel, exclusionModeLabel } from '../lib/labels'
import type { AnomalyDraft } from '../lib/types'

const TYPES = [
  'health',
  'finance',
  'relationship',
  'work',
  'environment',
  'plan_disruption',
  'other',
]

const EXCLUSION_MODES = ['metrics', 'none', 'all']

interface Props {
  value: AnomalyDraft | null
  onChange: (value: AnomalyDraft | null) => void
  disabled?: boolean
}

export function AnomalyBlock({ value, onChange, disabled }: Props) {
  const { t } = useTranslation()
  const enabled = value !== null
  const current: AnomalyDraft = value ?? { type: 'other', impact: 3 }

  const update = (patch: Partial<AnomalyDraft>) => onChange({ ...current, ...patch })

  return (
    <div className="anomaly-block">
      <label className="row" style={{ cursor: 'pointer' }}>
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled}
          style={{ width: 'auto' }}
          onChange={(event) =>
            onChange(
              event.target.checked
                ? { type: 'other', impact: 3, exclusion_mode: 'metrics' }
                : null,
            )
          }
        />
        <span>{t('anomaly.question')}</span>
      </label>

      {enabled && (
        <>
          <div className="grid-2" style={{ marginTop: 'var(--space-3)' }}>
            <div>
              <label>{t('anomaly.type')}</label>
              <select
                value={current.type}
                disabled={disabled}
                onChange={(event) => update({ type: event.target.value })}
              >
                {TYPES.map((type) => (
                  <option key={type} value={type}>
                    {anomalyTypeLabel(type)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label>{t('anomaly.impact')}</label>
              <select
                value={String(current.impact)}
                disabled={disabled}
                onChange={(event) => update({ impact: Number(event.target.value) })}
              >
                {[1, 2, 3, 4, 5].map((level) => (
                  <option key={level} value={level}>
                    {level}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ marginTop: 'var(--space-3)' }}>
            <label>{t('anomaly.exclusion_mode')}</label>
            <select
              value={current.exclusion_mode ?? 'metrics'}
              disabled={disabled}
              onChange={(event) =>
                update({ exclusion_mode: event.target.value as AnomalyDraft['exclusion_mode'] })
              }
            >
              {EXCLUSION_MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {exclusionModeLabel(mode)}
                </option>
              ))}
            </select>
          </div>

          <div style={{ marginTop: 'var(--space-3)' }}>
            <label>{t('anomaly.cause')}</label>
            <input
              type="text"
              value={current.cause_note ?? ''}
              disabled={disabled}
              onChange={(event) => update({ cause_note: event.target.value })}
            />
          </div>

          <p className="anomaly-note">{t('anomaly.note')}</p>
        </>
      )}
    </div>
  )
}
