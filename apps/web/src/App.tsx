/** 应用外壳：把解锁页与录入/列表页串起来（第 11 章）。
 *
 * 只做三件事：判断是否已解锁、把「空闲自动锁」如实反映到界面上、
 * 在 423 出现时把用户送回解锁页（而不是弹一个看不懂的报错）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { BackupSettings } from './components/BackupSettings'
import { ChainView } from './components/ChainView'
import { Dashboard } from './components/Dashboard'
import { Decisions } from './components/Decisions'
import { EntryList } from './components/EntryList'
import { EgressSettings } from './components/EgressSettings'
import { ExitButton } from './components/ExitButton'
import { FormRenderer } from './components/FormRenderer'
import { GoalsPlans } from './components/GoalsPlans'
import { JudgmentReview } from './components/JudgmentReview'
import { LlmSettings } from './components/LlmSettings'
import { LocalePicker } from './components/LocalePicker'
import { PeopleNetwork } from './components/PeopleNetwork'
import { PermissionTransferWizard } from './components/PermissionTransferWizard'
import { ReportReader } from './components/ReportReader'
import { ResourceLedger } from './components/ResourceLedger'
import { SelfCheck } from './components/SelfCheck'
import { ThemeEditor } from './components/ThemeEditor'
import { Timeline } from './components/Timeline'
import { Today } from './components/Today'
import { UnlockPage } from './components/UnlockPage'
import { setLocale } from './i18n'
import { api } from './lib/api'
import { formTitle } from './lib/labels'
import { getToken } from './lib/session'
import { applyAgeBand } from './theme/apply'
import {
  ApiError,
  type Entry,
  type EntryFilters,
  type FormDef,
  type UnlockResult,
} from './lib/types'

const DEFAULT_FILTERS: EntryFilters = { limit: 50 }

/** 法规敏感线：满 14 周岁即不再需要监护人同意（与后端 `CONSENT_AGE` 一致）。 */
const CONSENT_AGE = 14

/**
 * 是否提示办理权限转移（第 6.4 节，P6 W7）。
 *
 * 有 `birth_year` 时按年龄**精确**判断是否已满 14 周岁；缺失（早期档案或未填）
 * 时退回启发式（非儿童档即视为已跨档），保持旧行为不变。
 */
function canSuggestTransfer(active: UnlockResult): boolean {
  if (active.age_band === 'child') return false
  if (typeof active.birth_year === 'number') {
    return new Date().getFullYear() - active.birth_year >= CONSENT_AGE
  }
  return true
}

// 页签：录入沿用原有「列表 + 表单」双栏，其余是 P1/P2 新增页面（第 11.1 节）。
const TABS = [
  'record',
  'today',
  'dashboard',
  'timeline',
  'goals',
  'resources',
  'reports',
  'selfcheck',
  'judgments',
  'decisions',
  'people',
  'chain',
  'appearance',
  'backup',
  'egress',
  'llm',
  'transfer',
] as const
type Tab = (typeof TABS)[number]

/** 儿童档隐藏的页签（第 11.2 节）：出站设置、自我认知校验、判断复核、权限转移。
 *  出站设置是硬要求（第 7.7 节）；权限转移属于成人本人的操作，儿童档不展示；
 *  后两者是偏成人反思/元认知的内容。 */
const CHILD_HIDDEN: readonly Tab[] = ['egress', 'llm', 'selfcheck', 'judgments', 'transfer', 'resources']

/** 儿童档的落地页：从「今日」开始，而不是先看到录入表单。 */
const CHILD_HOME: Tab = 'today'

/** 朗读（W5b）面向小孩与特殊人群：儿童与长者档提供「读给我听」。 */
function supportsReadAloud(ageBand: string): boolean {
  return ageBand === 'child' || ageBand === 'senior'
}

export default function App() {
  const { t } = useTranslation()
  const [tokenReady, setTokenReady] = useState(() => Boolean(getToken()))
  const [active, setActive] = useState<UnlockResult | null>(null)
  const [forms, setForms] = useState<FormDef[]>([])
  const [activeFormId, setActiveFormId] = useState('')
  const [entries, setEntries] = useState<Entry[]>([])
  const [filters, setFilters] = useState<EntryFilters>(DEFAULT_FILTERS)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [hiddenCount, setHiddenCount] = useState(0)
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [tab, setTab] = useState<Tab>('record')

  const activeForm = useMemo(
    () => forms.find((form) => form.id === activeFormId) ?? null,
    [forms, activeFormId],
  )

  const visibleTabs = useMemo(
    () => (active?.age_band === 'child' ? TABS.filter((item) => !CHILD_HIDDEN.includes(item)) : TABS),
    [active?.age_band],
  )

  /** 统一的错误处理：423 一律回解锁页，其余原样展示（第 12.1 节）。 */
  const handleError = useCallback(
    (problem: unknown, fallback: string) => {
      if (problem instanceof ApiError) {
        if (problem.locked) {
          setMessage({ kind: 'error', text: t('notice.locked') })
          setActive(null)
          return
        }
        setMessage({ kind: 'error', text: problem.message })
        return
      }
      setMessage({ kind: 'error', text: fallback })
    },
    [t],
  )

  const notifyOk = useCallback((text: string) => {
    setMessage({ kind: 'ok', text })
  }, [])

  const loadEntries = useCallback(
    async (nextFilters: EntryFilters, append: boolean) => {
      setLoading(true)
      try {
        const page = await api.listEntries(nextFilters)
        setEntries((previous) => (append ? [...previous, ...page.items] : page.items))
        setNextCursor(page.next_cursor)
        setHiddenCount(page.hidden_count ?? 0)
        setMessage(null)
      } catch (problem) {
        handleError(problem, t('error.load_entries'))
      } finally {
        setLoading(false)
      }
    },
    [handleError, t],
  )

  useEffect(() => {
    if (!active) return
    void loadEntries(filters, false)
  }, [active, filters, loadEntries])

  const handleUnlocked = async (result: UnlockResult) => {
    try {
      const list = await api.listForms(result.age_band)
      setForms(list.items)
      // 13 个领域都有表单后，默认选中「今日打卡」，而不是按文件名的第一个（career）。
      const preferred = list.items.find((form) => form.domain === 'daily') ?? list.items[0]
      setActiveFormId(preferred?.id ?? '')
      setLocale(result.locale)
      applyAgeBand(result.age_band)
      setActive(result)
      setFilters(DEFAULT_FILTERS)
      setHiddenCount(0)
      setTab(result.age_band === 'child' ? CHILD_HOME : 'record')
      setMessage(null)
    } catch (problem) {
      handleError(problem, t('error.load_forms'))
    }
  }

  const handleLock = async () => {
    if (!active) return
    try {
      await api.lock(active.profile_id)
    } catch (problem) {
      handleError(problem, t('error.lock'))
    }
    applyAgeBand(null)
    setActive(null)
    setEntries([])
    setHiddenCount(0)
    setTab('record')
    setMessage({ kind: 'ok', text: t('notice.locked_ok') })
  }

  const handleRestored = (text: string) => {
    // 恢复已在服务端锁定会话，这里同步回解锁页，让用户重新解锁
    setActive(null)
    setEntries([])
    setHiddenCount(0)
    setTab('record')
    setMessage({ kind: 'ok', text })
  }

  const handleSubmit = async (values: Record<string, unknown>) => {
    if (!activeForm) return
    setSubmitting(true)
    try {
      const entry = await api.submitForm(activeForm.id, values)
      setEntries((previous) => [entry, ...previous])
      setMessage({ kind: 'ok', text: t('notice.saved') })
    } catch (problem) {
      handleError(problem, t('error.save'))
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (entry: Entry) => {
    try {
      await api.deleteEntry(entry.id)
      setEntries((previous) => previous.filter((item) => item.id !== entry.id))
      setMessage({ kind: 'ok', text: t('notice.deleted') })
    } catch (problem) {
      handleError(problem, t('error.delete'))
    }
  }

  const handleFilterChange = (next: EntryFilters) => {
    setNextCursor(null)
    setFilters(next)
  }

  const handleLoadMore = () => {
    if (!nextCursor) return
    void loadEntries({ ...filters, cursor: nextCursor }, true)
  }

  if (!tokenReady) {
    return (
      <>
        <div className="gate-exit">
          <ExitButton onNotice={(kind, text) => setMessage({ kind, text })} />
        </div>
        <UnlockPage
          hasToken={false}
          onTokenChanged={() => setTokenReady(Boolean(getToken()))}
          onUnlocked={handleUnlocked}
        />
      </>
    )
  }

  if (!active) {
    return (
      <>
        <div className="gate-exit">
          <ExitButton onNotice={(kind, text) => setMessage({ kind, text })} />
        </div>
        {message && (
          <div style={{ maxWidth: 520, margin: 'var(--space-4) auto 0' }}>
            <div className={`notice ${message.kind}`}>{message.text}</div>
          </div>
        )}
        <UnlockPage
          hasToken
          onTokenChanged={() => setTokenReady(Boolean(getToken()))}
          onUnlocked={handleUnlocked}
        />
      </>
    )
  }

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">{t('app.brand')}</span>
        <span className="pill">{active.alias}</span>
        <span className="spacer" />
        <LocalePicker
          profileId={active.profile_id}
          onNotice={(kind, text) => setMessage({ kind, text })}
        />
        <span className="muted">{t('app.idle_lock', { minutes: active.idle_lock_minutes })}</span>
        <button onClick={() => void handleLock()}>{t('app.lock')}</button>
        <ExitButton onNotice={(kind, text) => setMessage({ kind, text })} />
      </header>

      {active.role === 'guardian' && (
        <div className="guardian-banner">
          <span className="title">{t('guardian.viewer.title')}</span>
          <span className="scope">{t('guardian.viewer.scope')}</span>
          <span className="limit">{t('guardian.viewer.limit')}</span>
        </div>
      )}

      <nav className="tabs" aria-label={t('app.brand')}>
        {visibleTabs.map((item) => (
          <button
            key={item}
            className={item === tab ? 'tab active' : 'tab'}
            aria-current={item === tab ? 'page' : undefined}
            onClick={() => setTab(item)}
          >
            {t(`nav.${item}`)}
          </button>
        ))}
      </nav>

      {/* 6.4 跨档提示：本人会话、挂着监护人包装、且已满 14 周岁（有 birth_year 时精确判断）时，建议办理权限转移。 */}
      {active.role === 'owner' && active.guardian_linked && canSuggestTransfer(active) && (
        <div className="transfer-suggest">
          <span>{t('transfer.suggest')}</span>
          <button className="ghost" onClick={() => setTab('transfer')}>
            {t('transfer.suggest.action')}
          </button>
        </div>
      )}

      {message && (
        <div style={{ padding: 'var(--space-3) var(--space-5) 0' }}>
          <div className={`notice ${message.kind}`}>{message.text}</div>
        </div>
      )}

      {tab === 'record' && (
        <div className="content">
          <EntryList
            entries={entries}
            loading={loading}
            filters={filters}
            nextCursor={nextCursor}
            hiddenCount={hiddenCount}
            readAloud={supportsReadAloud(active.age_band)}
            onFilterChange={handleFilterChange}
            onLoadMore={handleLoadMore}
            onDelete={(entry) => void handleDelete(entry)}
          />

          <div>
            <div className="card">
              <h2>{t('app.record_one')}</h2>
              <div className="row wrap">
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
            </div>

            {activeForm && (
              <FormRenderer
                key={activeForm.id}
                form={activeForm}
                ageBand={active.age_band}
                submitting={submitting}
                onSubmit={(values) => void handleSubmit(values)}
              />
            )}
          </div>
        </div>
      )}

      {tab === 'today' && (
        <Today ageBand={active.age_band} onError={handleError} onNotice={notifyOk} />
      )}

      {tab === 'dashboard' && <Dashboard onError={handleError} />}

      {tab === 'timeline' && (
        <div className="content single">
          <Timeline onError={handleError} onNotice={notifyOk} />
        </div>
      )}

      {tab === 'goals' && <GoalsPlans onError={handleError} onNotice={notifyOk} />}
      {tab === 'resources' && <ResourceLedger onError={handleError} onNotice={notifyOk} />}

      {tab === 'reports' && (
        <div className="content single">
          <ReportReader
            readAloud={supportsReadAloud(active.age_band)}
            ageBand={active.age_band}
            role={active.role}
            onError={handleError}
            onNotice={notifyOk}
          />
        </div>
      )}

      {tab === 'egress' && <EgressSettings onError={handleError} onNotice={notifyOk} />}

      {tab === 'llm' && <LlmSettings onError={handleError} onNotice={notifyOk} />}

      {tab === 'selfcheck' && <SelfCheck onError={handleError} onNotice={notifyOk} />}

      {tab === 'judgments' && (
        <div className="content single">
          <JudgmentReview onError={handleError} />
        </div>
      )}

      {tab === 'appearance' && <ThemeEditor onError={handleError} onNotice={notifyOk} />}

      {tab === 'decisions' && <Decisions onError={handleError} onNotice={notifyOk} />}

      {tab === 'people' && <PeopleNetwork onError={handleError} onNotice={notifyOk} />}

      {tab === 'chain' && <ChainView onError={handleError} />}

      {tab === 'backup' && (
        <BackupSettings onError={handleError} onNotice={notifyOk} onRestored={handleRestored} />
      )}

      {tab === 'transfer' && (
        <div className="content single">
          <PermissionTransferWizard active={active} onError={handleError} onNotice={notifyOk} />
        </div>
      )}
    </div>
  )
}
