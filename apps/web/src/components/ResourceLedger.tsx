/** 资源/资产台账（第 5 章）：先登记「有什么、现在多少」，之后录入只记变化。
 *
 * 这里只做初始登记与查看；日常增减走「录入 → 资源盘点」表单，
 * 后端把 delta 累加到 current_value，并把变化后的余额写回该条记录。
 */

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import { invalidateRefCache } from '../lib/refs'
import type { ResourceAccount } from '../lib/types'

const KINDS = ['asset', 'money', 'tool', 'space', 'time', 'energy'] as const

interface Props {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}

export function ResourceLedger({ onError, onNotice }: Props) {
  const { t } = useTranslation()
  const [items, setItems] = useState<ResourceAccount[]>([])
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [draft, setDraft] = useState({
    name: '',
    kind: 'money',
    unit: '',
    initial_value: '',
  })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const list = await api.listResources()
      setItems(list.items)
    } catch (problem) {
      onError(problem, t('error.load_forms'))
    } finally {
      setLoading(false)
    }
  }, [onError, t])

  useEffect(() => {
    void load()
  }, [load])

  const handleCreate = async () => {
    if (!draft.name.trim()) return
    setSubmitting(true)
    try {
      await api.createResource({
        name: draft.name.trim(),
        kind: draft.kind,
        unit: draft.unit.trim() || null,
        initial_value: draft.initial_value === '' ? 0 : Number(draft.initial_value),
      })
      setDraft({ name: '', kind: 'money', unit: '', initial_value: '' })
      setShowForm(false)
      invalidateRefCache('resource')
      onNotice(t('resources.created'))
      await load()
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setSubmitting(false)
    }
  }

  const kindLabel = (kind: string | null) =>
    kind ? t(`option.resource_kind.${kind}`) : t('resources.none')

  return (
    <div className="content">
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2>{t('resources.title')}</h2>
          <button onClick={() => setShowForm((previous) => !previous)}>
            {t('resources.new')}
          </button>
        </div>
        <p className="muted">{t('resources.hint')}</p>
        <p className="muted">{t('resources.examples')}</p>

        {showForm && (
          <div className="field-block">
            <div className="field">
              <label>{t('resources.name')}</label>
              <input
                value={draft.name}
                onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              />
            </div>
            <div className="grid-2">
              <div>
                <label>{t('resources.kind')}</label>
                <select
                  value={draft.kind}
                  onChange={(event) => setDraft({ ...draft, kind: event.target.value })}
                >
                  {KINDS.map((kind) => (
                    <option key={kind} value={kind}>
                      {t(`option.resource_kind.${kind}`)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label>{t('resources.unit')}</label>
                <input
                  value={draft.unit}
                  placeholder={t('resources.unit_placeholder')}
                  onChange={(event) => setDraft({ ...draft, unit: event.target.value })}
                />
              </div>
            </div>
            <div className="field">
              <label>{t('resources.initial')}</label>
              <input
                type="number"
                value={draft.initial_value}
                onChange={(event) => setDraft({ ...draft, initial_value: event.target.value })}
              />
            </div>
            <div className="row">
              <button
                className="primary"
                disabled={submitting || !draft.name.trim()}
                onClick={() => void handleCreate()}
              >
                {t('resources.save')}
              </button>
              <button disabled={submitting} onClick={() => setShowForm(false)}>
                {t('resources.cancel')}
              </button>
            </div>
          </div>
        )}

        {loading && <p className="muted">{t('entry.loading')}</p>}
        {!loading && items.length === 0 && <p className="muted">{t('resources.empty')}</p>}
        {!loading &&
          items.map((item) => (
            <div
              key={item.id}
              className="row"
              style={{ justifyContent: 'space-between', padding: 'var(--space-2) 0' }}
            >
              <span>{item.name}</span>
              <span className="muted">{kindLabel(item.kind)}</span>
              <strong>
                {item.current_value}
                {item.unit ?? ''}
              </strong>
            </div>
          ))}
      </div>
    </div>
  )
}
