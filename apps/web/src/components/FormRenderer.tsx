/** 表单渲染器（第 6 章）：按 YAML 定义渲染录入界面。
 *
 * 未知字段、校验都以服务端为准；这里只负责把定义画出来、把值收齐，
 * 并在提交时把填了值的字段挑出来（空值不提交）。
 */

import { useMemo, useState } from 'react'

import { t } from '../i18n'
import { api } from '../lib/api'
import { resolveOptions, type Option } from '../lib/dictionaries'
import {
  numberBounds,
  pruneValues,
  resolveWidget,
  stepperStep,
  visibleSections,
} from '../lib/formUtils'
import { fieldLabel, formTitle } from '../lib/labels'
import { type RefFieldType } from '../lib/refs'
import type { AnomalyDraft, FieldDef, FormDef } from '../lib/types'
import { AnomalyBlock } from './AnomalyBlock'
import { ReadAloud } from './ReadAloud'
import { RefPicker } from './RefPicker'

interface Props {
  form: FormDef
  ageBand?: string
  submitting: boolean
  onSubmit: (values: Record<string, unknown>) => void
}

type Value = string | number | string[] | null

/** 落库为外键 id 的字段类型（第 5 章）：必须走选择器，不能填名字。 */
const REF_FIELD_TYPES: readonly RefFieldType[] = [
  'person',
  'hobby',
  'skill',
  'goal',
  'plan',
  'resource',
]

function Scale5({
  value,
  onChange,
  disabled,
}: {
  value: number | null
  onChange: (value: number) => void
  disabled?: boolean
}) {
  return (
    <div className="scale5">
      {[1, 2, 3, 4, 5].map((level) => (
        <button
          key={level}
          type="button"
          disabled={disabled}
          className={value === level ? 'on' : ''}
          onClick={() => onChange(level)}
        >
          {level}
        </button>
      ))}
    </div>
  )
}

/** 儿童档量表：五个表情按钮代表 1..5（第 6.2 节）。 */
const EMOJI_FACES = ['😢', '😕', '😐', '🙂', '😄'] as const

function Emoji5({
  value,
  onChange,
  disabled,
}: {
  value: number | null
  onChange: (value: number) => void
  disabled?: boolean
}) {
  return (
    <div className="emoji5">
      {EMOJI_FACES.map((face, index) => {
        const level = index + 1
        return (
          <button
            key={level}
            type="button"
            disabled={disabled}
            className={value === level ? 'on' : ''}
            aria-label={String(level)}
            onClick={() => onChange(level)}
          >
            {face}
          </button>
        )
      })}
    </div>
  )
}

/** 儿童档数量输入：大号减 / 加按钮，不做键盘输入（第 6.2 节）。 */
function Stepper({
  field,
  value,
  onChange,
  disabled,
}: {
  field: FieldDef
  value: number | null
  onChange: (value: number) => void
  disabled?: boolean
}) {
  const bounds = numberBounds(field)
  const step = stepperStep(field)

  const bump = (delta: number) => {
    let next = (value ?? 0) + delta
    if (bounds.min !== undefined && next < bounds.min) next = bounds.min
    if (bounds.max !== undefined && next > bounds.max) next = bounds.max
    onChange(next)
  }

  return (
    <div className="stepper">
      <button
        type="button"
        className="step-btn"
        disabled={disabled}
        aria-label={t('form.stepper.minus')}
        onClick={() => bump(-step)}
      >
        −
      </button>
      <span className="step-value">{value ?? 0}</span>
      <button
        type="button"
        className="step-btn"
        disabled={disabled}
        aria-label={t('form.stepper.plus')}
        onClick={() => bump(step)}
      >
        ＋
      </button>
    </div>
  )
}

/** 儿童档选项：把下拉换成一大排图标 / 文字按钮（第 6.2 节）。 */
function IconChoice({
  options,
  value,
  onChange,
  disabled,
}: {
  options: Option[]
  value: string | null
  onChange: (value: string) => void
  disabled?: boolean
}) {
  return (
    <div className="icons">
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          disabled={disabled}
          className={value === option.key ? 'on' : ''}
          onClick={() => onChange(option.key)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** 儿童档图片：拍照 / 选图后加密上传，只留附件 id（第 7.2 节第 5 条）。 */
function ImagePicker({
  value,
  onChange,
  disabled,
}: {
  value: string | null
  onChange: (value: Value) => void
  disabled?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const handleFile = async (file: File | undefined) => {
    if (!file) return
    setBusy(true)
    setError('')
    try {
      const attachment = await api.uploadAttachment(file, 'image')
      onChange(attachment.id)
    } catch {
      setError(t('form.image.failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="file">
      <label className="file-btn">
        {busy ? t('form.image.uploading') : t('form.image.pick')}
        <input
          type="file"
          accept="image/*"
          capture="environment"
          disabled={disabled || busy}
          style={{ display: 'none' }}
          onChange={(event) => void handleFile(event.target.files?.[0])}
        />
      </label>
      {value && (
        <div className="row">
          <span className="muted">{t('form.image.attached')}</span>
          <button type="button" className="ghost" onClick={() => onChange(null)}>
            {t('form.image.remove')}
          </button>
        </div>
      )}
      {error && <div className="notice error">{error}</div>}
    </div>
  )
}

function FieldInput({
  field,
  value,
  onChange,
  disabled,
  ageBand,
}: {
  field: FieldDef
  value: Value
  onChange: (value: Value) => void
  disabled?: boolean
  ageBand?: string
}) {
  const label = fieldLabel(field.key)
  const widget = resolveWidget(field, ageBand)
  // 端到端测试用的稳定定位钩子（P6 W6.3）：字段的 label 没有与输入框关联，
  // 仅靠文案在多个同类字段间无法区分，这里给每个字段容器一个按 key 命名的钩子。
  const testId = `field-${field.key}`

  // 儿童档控件（第 6.2 节）：同一份 YAML 定义，按年龄档换成更大更直观的样子。
  if (widget === 'emoji5') {
    return (
      <div className="field" data-testid={testId}>
        <label>{label}</label>
        <Emoji5
          value={typeof value === 'number' ? value : null}
          disabled={disabled}
          onChange={onChange}
        />
      </div>
    )
  }

  if (widget === 'stepper') {
    return (
      <div className="field" data-testid={testId}>
        <label>
          {label}
          {field.unit ? `（${field.unit}）` : ''}
        </label>
        <Stepper
          field={field}
          value={typeof value === 'number' ? value : null}
          disabled={disabled}
          onChange={onChange}
        />
      </div>
    )
  }

  if (widget === 'icons') {
    const options = resolveOptions(field.options)
    if (options.length) {
      return (
        <div className="field" data-testid={testId}>
          <label>{label}</label>
          <IconChoice
            options={options}
            value={typeof value === 'string' ? value : null}
            disabled={disabled}
            onChange={onChange}
          />
        </div>
      )
    }
    // 没有可解析的选项时退回普通文本输入（同 choice 分支的兜底）。
  }

  if (widget === 'file') {
    return (
      <div className="field" data-testid={testId}>
        <label>{label}</label>
        <ImagePicker
          value={typeof value === 'string' ? value : null}
          disabled={disabled}
          onChange={onChange}
        />
      </div>
    )
  }

  if (field.type === 'scale5') {
    return (
      <div className="field" data-testid={testId}>
        <label>{label}</label>
        <Scale5
          value={typeof value === 'number' ? value : null}
          disabled={disabled}
          onChange={onChange}
        />
      </div>
    )
  }

  if (field.type === 'longtext') {
    return (
      <div className="field" data-testid={testId}>
        <label>{label}</label>
        <textarea
          value={typeof value === 'string' ? value : ''}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    )
  }

  if (field.type === 'choice') {
    const options = resolveOptions(field.options)
    if (options.length) {
      return (
        <div className="field" data-testid={testId}>
          <label>{label}</label>
          <select
            value={typeof value === 'string' ? value : ''}
            disabled={disabled}
            onChange={(event) => onChange(event.target.value)}
          >
            <option value="">{t('form.unselected')}</option>
            {options.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      )
    }
    return (
      <div className="field" data-testid={testId}>
        <label>{label}</label>
        <input
          type="text"
          value={typeof value === 'string' ? value : ''}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    )
  }

  if (field.type === 'multichoice' || field.type === 'tags') {
    const options = resolveOptions(field.options)
    const selected = Array.isArray(value) ? value : []
    if (options.length) {
      return (
        <div className="field" data-testid={testId}>
          <label>{label}</label>
          <div className="row wrap">
            {options.map((option) => {
              const on = selected.includes(option.key)
              return (
                <label key={option.key} className="row" style={{ cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    style={{ width: 'auto' }}
                    checked={on}
                    disabled={disabled}
                    onChange={() =>
                      onChange(
                        on
                          ? selected.filter((item) => item !== option.key)
                          : [...selected, option.key],
                      )
                    }
                  />
                  <span>{option.label}</span>
                </label>
              )
            })}
          </div>
        </div>
      )
    }
    return (
      <div className="field" data-testid={testId}>
        <label>{label}</label>
        <input
          type="text"
          placeholder={t('form.comma_separated')}
          value={selected.join(',')}
          disabled={disabled}
          onChange={(event) =>
            onChange(
              event.target.value
                .split(',')
                .map((item) => item.trim())
                .filter(Boolean),
            )
          }
        />
      </div>
    )
  }

  const isNumber = field.type === 'number' || field.type === 'money' || field.type === 'duration'
  if (isNumber) {
    const bounds = numberBounds(field)
    return (
      <div className="field" data-testid={testId}>
        <label>
          {label}
          {field.unit ? `（${field.unit}）` : ''}
        </label>
        <input
          type="number"
          min={bounds.min}
          max={bounds.max}
          step={bounds.step}
          value={typeof value === 'number' ? value : ''}
          disabled={disabled}
          onChange={(event) =>
            onChange(event.target.value === '' ? null : Number(event.target.value))
          }
        />
      </div>
    )
  }

  if (field.type === 'date' || field.type === 'time') {
    return (
      <div className="field" data-testid={testId}>
        <label>{label}</label>
        <input
          type={field.type}
          value={typeof value === 'string' ? value : ''}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    )
  }

  // person / hobby / skill / goal / plan：落库的是外键 id，用选择器（可新建）。
  if (REF_FIELD_TYPES.includes(field.type as RefFieldType)) {
    return (
      <RefPicker
        field={field}
        value={typeof value === 'string' ? value : null}
        disabled={disabled}
        onChange={onChange}
      />
    )
  }

  // text / image：P0 先给文本输入，图片选择在带上传的界面里做。
  return (
    <div className="field" data-testid={testId}>
      <label>{label}</label>
      <input
        type="text"
        value={typeof value === 'string' ? value : ''}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  )
}

export function FormRenderer({ form, ageBand, submitting, onSubmit }: Props) {
  const [values, setValues] = useState<Record<string, Value>>({})
  const [anomaly, setAnomaly] = useState<AnomalyDraft | null>(null)
  const [error, setError] = useState('')

  const sections = useMemo(() => visibleSections(form, ageBand), [form, ageBand])

  // 儿童档：把表单标题与各区块名串成一段说明，供「读给我听」（P5 W5b，方案 5b.6）。
  const speechText = useMemo(() => {
    if (ageBand !== 'child') return ''
    return [formTitle(form.title), ...sections.map((section) => fieldLabel(section.id))]
      .filter(Boolean)
      .join('。')
  }, [ageBand, form, sections])

  const setValue = (key: string, value: Value) =>
    setValues((previous) => ({ ...previous, [key]: value }))

  const handleSubmit = () => {
    const payload = pruneValues(values)
    if (!Object.keys(payload).length && !anomaly) {
      setError(t('form.error_empty'))
      return
    }
    setError('')
    if (form.anomaly && anomaly) {
      payload.anomaly = anomaly
    }
    onSubmit(payload)
  }

  const handleReset = () => {
    setValues({})
    setAnomaly(null)
    setError('')
  }

  return (
    <div className="card">
      <h2>{formTitle(form.title)}</h2>
      {speechText && (
        <div style={{ marginBottom: 'var(--space-3)' }}>
          <ReadAloud text={speechText} lang="zh-CN" />
        </div>
      )}

      {sections.map((section) => (
        <div key={section.id}>
          <h3>{fieldLabel(section.id)}</h3>
          {section.fields.map((field) => (
            <FieldInput
              key={field.key}
              field={field}
              ageBand={ageBand}
              value={values[field.key] ?? null}
              disabled={submitting}
              onChange={(value) => setValue(field.key, value)}
            />
          ))}
        </div>
      ))}

      {form.anomaly && (
        <div style={{ marginTop: 'var(--space-4)' }}>
          <AnomalyBlock value={anomaly} onChange={setAnomaly} disabled={submitting} />
        </div>
      )}

      {error && (
        <div className="notice error" style={{ marginTop: 'var(--space-3)' }}>
          {error}
        </div>
      )}

      <div className="row" style={{ marginTop: 'var(--space-4)' }}>
        <button className="primary" onClick={handleSubmit} disabled={submitting}>
          {submitting ? t('form.saving') : t('form.save')}
        </button>
        <button className="ghost" onClick={handleReset} disabled={submitting}>
          {t('form.clear')}
        </button>
      </div>
    </div>
  )
}
