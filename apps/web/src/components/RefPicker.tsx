/** 长期对象选择器（第 6.2 节 person / hobby / skill / goal / plan / resource）。
 *
 * 这些字段落库的是外键 id，直接填名字会触发外键约束失败（第 5 章），
 * 所以录入表单到这里统一换成「下拉选择 + 就地新建」：选中的值就是对象 id。
 */

import { useEffect, useState } from 'react'

import { t } from '../i18n'
import { fieldLabel } from '../lib/labels'
import {
  cachedRefOptions,
  createRefOption,
  loadRefOptions,
  type RefFieldType,
  type RefOption,
} from '../lib/refs'
import type { FieldDef } from '../lib/types'

interface Props {
  field: FieldDef
  value: string | null
  disabled?: boolean
  onChange: (value: string | null) => void
}

export function RefPicker({ field, value, disabled, onChange }: Props) {
  const fieldType = field.type as RefFieldType
  const [options, setOptions] = useState<RefOption[]>(
    () => cachedRefOptions(fieldType) ?? [],
  )
  const [loading, setLoading] = useState(() => cachedRefOptions(fieldType) === null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const cached = cachedRefOptions(fieldType)
    if (cached) {
      setOptions(cached)
      setLoading(false)
      return
    }
    let alive = true
    setLoading(true)
    loadRefOptions(fieldType)
      .then((items) => {
        if (alive) {
          setOptions(items)
          setLoading(false)
        }
      })
      .catch(() => {
        if (alive) {
          setError(t('form.picker.failed'))
          setLoading(false)
        }
      })
    return () => {
      alive = false
    }
  }, [fieldType])

  const handleCreate = async () => {
    const text = name.trim()
    if (!text) return
    setBusy(true)
    setError('')
    try {
      const created = await createRefOption(fieldType, text)
      setOptions(cachedRefOptions(fieldType) ?? [created])
      onChange(created.id)
      setCreating(false)
      setName('')
    } catch {
      setError(t('form.picker.failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="field" data-testid={`field-${field.key}`}>
      <label>{fieldLabel(field.key)}</label>
      <div className="row">
        <select
          value={value ?? ''}
          disabled={disabled || loading}
          onChange={(event) => onChange(event.target.value || null)}
        >
          <option value="">{t('form.picker.placeholder')}</option>
          {options.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="ghost"
          disabled={disabled}
          onClick={() => setCreating((previous) => !previous)}
        >
          {t('form.picker.new')}
        </button>
      </div>

      {!loading && options.length === 0 && <div className="muted">{t('form.picker.none')}</div>}

      {creating && (
        <div className="row" style={{ marginTop: 'var(--space-2)' }}>
          <input
            type="text"
            placeholder={t('form.picker.name')}
            value={name}
            disabled={busy || disabled}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void handleCreate()
            }}
          />
          <button
            type="button"
            className="primary"
            disabled={busy || disabled || !name.trim()}
            onClick={() => void handleCreate()}
          >
            {t('form.picker.create')}
          </button>
          <button
            type="button"
            className="ghost"
            disabled={busy}
            onClick={() => {
              setCreating(false)
              setName('')
            }}
          >
            {t('form.picker.cancel')}
          </button>
        </div>
      )}

      {error && <div className="notice error">{error}</div>}
    </div>
  )
}
