/** 个人基础信息（决策评估与自我认知的前提条件）。
 *
 * 只收结构化选项与短职位名：这些字段会作为本地事实进入决策分析，
 * 所以不放自由文本备注，出站时按现有白名单收窄（第 7.4 节）。
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import type { BasicsOptions, ProfileBasics as Basics } from '../lib/types'

interface Props {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}

const SELECT_FIELDS = [
  'industry',
  'education_level',
  'work_style',
  'risk_tolerance',
  'family_status',
] as const

type SelectField = (typeof SELECT_FIELDS)[number]

export function ProfileBasics({ onError, onNotice }: Props) {
  const { t } = useTranslation()
  const [form, setForm] = useState<Basics | null>(null)
  const [options, setOptions] = useState<BasicsOptions | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api
      .getBasics()
      .then((result) => {
        setForm(result.item)
        setOptions(result.options)
      })
      .catch((problem) => onError(problem, '加载基础信息失败'))
  }, [onError])

  if (!form || !options) return null

  const toggleConstraint = (code: string) => {
    const has = form.constraints.includes(code)
    setForm({
      ...form,
      constraints: has
        ? form.constraints.filter((item) => item !== code)
        : [...form.constraints, code],
    })
  }

  const save = async () => {
    setSaving(true)
    try {
      const result = await api.updateBasics(form)
      setForm(result.item)
      onNotice(t('basics.saved'))
    } catch (problem) {
      onError(problem, '保存基础信息失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card">
      <h2>{t('basics.title')}</h2>
      <p className="muted">{t('basics.hint')}</p>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
          gap: 'var(--space-3)',
        }}
      >
        {SELECT_FIELDS.map((field: SelectField) => (
          <div className="field-block" key={field}>
            <label htmlFor={`basics-${field}`}>{t(`basics.${field}`)}</label>
            <select
              id={`basics-${field}`}
              value={form[field] ?? ''}
              disabled={saving}
              onChange={(event) =>
                setForm({ ...form, [field]: event.target.value || null } as Basics)
              }
            >
              <option value="">{t('basics.unset')}</option>
              {options[field].map((code) => (
                <option key={code} value={code}>
                  {t(`basics.${field}.${code}`)}
                </option>
              ))}
            </select>
          </div>
        ))}

        <div className="field-block">
          <label htmlFor="basics-job">{t('basics.job_title')}</label>
          <input
            id="basics-job"
            maxLength={30}
            value={form.job_title ?? ''}
            disabled={saving}
            placeholder={t('basics.job_title_ph')}
            onChange={(event) => setForm({ ...form, job_title: event.target.value || null })}
          />
        </div>

        <div className="field-block">
          <label htmlFor="basics-dependents">{t('basics.dependents')}</label>
          <select
            id="basics-dependents"
            value={form.dependents ?? ''}
            disabled={saving}
            onChange={(event) =>
              setForm({
                ...form,
                dependents: event.target.value === '' ? null : Number(event.target.value),
              })
            }
          >
            <option value="">{t('basics.unset')}</option>
            {[0, 1, 2, 3, 4].map((count) => (
              <option key={count} value={count}>
                {count === 4
                  ? t('basics.dependents_4plus')
                  : t('basics.dependents_count', { count })}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="field-block" style={{ marginTop: 'var(--space-3)' }}>
        <div className="label">{t('basics.constraints')}</div>
        <div className="row wrap">
          {options.constraints.map((code) => (
            <label key={code} htmlFor={`basics-c-${code}`}>
              <input
                id={`basics-c-${code}`}
                type="checkbox"
                checked={form.constraints.includes(code)}
                disabled={saving}
                style={{ width: 'auto', marginRight: 'var(--space-2)' }}
                onChange={() => toggleConstraint(code)}
              />
              {t(`basics.constraints.${code}`)}
            </label>
          ))}
        </div>
      </div>

      <div className="row" style={{ marginTop: 'var(--space-4)', alignItems: 'baseline' }}>
        <button className="primary" onClick={() => void save()} disabled={saving}>
          {saving ? t('basics.saving') : t('basics.save')}
        </button>
        {form.updated_at && (
          <span className="muted">
            {t('basics.updated', { date: form.updated_at.slice(0, 16).replace('T', ' ') })}
          </span>
        )}
      </div>
    </div>
  )
}
