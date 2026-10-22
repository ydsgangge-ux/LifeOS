/** 权限转移向导（P5 W6、验收 A3）。
 *
 * 分四步：① 如实说明技术局限 → ② 选择动作（挂监护人 / 撤销监护人包装 / 调整可见范围）
 * → ③（按动作）填信息 → ④ 二次确认 → 结果。
 *
 * 关于「验证身份」：本向导的后端动作都要求**本人会话**（`_owner_session_dek`），
 * 换句话说解锁那一步已经验证过身份，这里不再重复索要口令（避免造一道假门槛）。
 * 只有本人解锁的会话能进这个页签。
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import { domainLabel } from '../lib/labels'
import type { GuardianPolicy, UnlockResult } from '../lib/types'

/** 可加白的领域（第 7.7 节）：dream 与 relation 永不加白，故不出现在这里。 */
const POLICY_DOMAINS = [
  'daily',
  'income',
  'expense',
  'health',
  'goal',
  'plan',
  'education',
  'career',
  'hobby',
  'idea',
  'resource',
]

type Action = 'add' | 'remove' | 'policy'
type Step = 'limit' | 'action' | 'detail' | 'confirm' | 'result'

interface Props {
  active: UnlockResult
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}

export function PermissionTransferWizard({ active, onError, onNotice }: Props) {
  const { t } = useTranslation()
  const [step, setStep] = useState<Step>('limit')
  const [action, setAction] = useState<Action | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const [guardianPassphrase, setGuardianPassphrase] = useState('')
  const [policy, setPolicy] = useState<GuardianPolicy>({
    visible_domains: [],
    allow_journal_text: false,
    allow_detail: false,
  })
  const [resultText, setResultText] = useState('')

  const goAction = () => {
    setError('')
    setStep('action')
  }

  const chooseAction = async (next: Action) => {
    setAction(next)
    setError('')
    if (next === 'policy') {
      // 读当前策略作为表单初值（本人会话可读）。
      try {
        const current = await api.getGuardianPolicy(active.profile_id)
        setPolicy(current.policy)
      } catch (problem) {
        onError(problem, t('transfer.policy_load_failed'))
        return
      }
    }
    setStep('detail')
  }

  const toggleDomain = (domain: string) => {
    setPolicy((previous) => ({
      ...previous,
      visible_domains: previous.visible_domains.includes(domain)
        ? previous.visible_domains.filter((item) => item !== domain)
        : [...previous.visible_domains, domain],
    }))
  }

  const goConfirm = () => {
    if (action === 'add' && guardianPassphrase.length < 6) {
      setError(t('unlock.guardian_passphrase_too_short'))
      return
    }
    setError('')
    setStep('confirm')
  }

  const submit = async () => {
    if (!action) return
    setBusy(true)
    setError('')
    try {
      if (action === 'add') {
        await api.addGuardian(active.profile_id, guardianPassphrase)
        setResultText(t('transfer.done.add'))
        onNotice(t('transfer.done.add'))
      } else if (action === 'remove') {
        await api.removeGuardian(active.profile_id)
        setResultText(t('transfer.done.remove'))
        onNotice(t('transfer.done.remove'))
      } else {
        await api.setGuardianPolicy(active.profile_id, policy)
        setResultText(t('transfer.done.policy'))
        onNotice(t('guardian.policy.updated'))
      }
      setStep('result')
    } catch (problem) {
      onError(problem, t('error.save'))
      setError(t('error.save'))
    } finally {
      setBusy(false)
    }
  }

  const actionLabel = (value: Action) => t(`transfer.action.${value}`)

  return (
    <div className="card">
      <h2>{t('transfer.title')}</h2>

      {step === 'limit' && (
        <>
          <div className="notice warn">{t('guardian.viewer.limit')}</div>
          <p className="muted">{t('transfer.limit_note')}</p>
          <div className="row" style={{ marginTop: 'var(--space-3)' }}>
            <button className="primary" onClick={goAction}>
              {t('transfer.next')}
            </button>
          </div>
        </>
      )}

      {step === 'action' && (
        <>
          <p className="muted">{t('transfer.action_hint')}</p>
          <div className="row wrap">
            {(['add', 'remove', 'policy'] as const).map((value) => (
              <button
                key={value}
                className={value === action ? 'primary' : ''}
                disabled={busy}
                onClick={() => void chooseAction(value)}
              >
                {actionLabel(value)}
              </button>
            ))}
          </div>
          {action === 'remove' && active.guardian_linked === false && (
            <p className="muted" style={{ marginTop: 'var(--space-2)' }}>
              {t('transfer.remove.none')}
            </p>
          )}
        </>
      )}

      {step === 'detail' && action && (
        <>
          <div className="section-title">{actionLabel(action)}</div>
          {action === 'add' && (
            <div className="field">
              <label>{t('unlock.guardian_passphrase')}</label>
              <input
                type="password"
                value={guardianPassphrase}
                onChange={(event) => setGuardianPassphrase(event.target.value)}
              />
              <p className="muted">{t('transfer.add.hint')}</p>
            </div>
          )}
          {action === 'remove' && <p className="muted">{t('transfer.remove.hint')}</p>}
          {action === 'policy' && (
            <>
              <label>{t('guardian.policy.visible_domains')}</label>
              <div className="row wrap">
                {POLICY_DOMAINS.map((domain) => {
                  const on = policy.visible_domains.includes(domain)
                  return (
                    <label key={domain} className="row" style={{ cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        style={{ width: 'auto' }}
                        checked={on}
                        onChange={() => toggleDomain(domain)}
                      />
                      <span>{domainLabel(domain)}</span>
                    </label>
                  )
                })}
              </div>
              <label className="row" style={{ cursor: 'pointer', marginTop: 'var(--space-3)' }}>
                <input
                  type="checkbox"
                  style={{ width: 'auto' }}
                  checked={policy.allow_journal_text}
                  onChange={(event) =>
                    setPolicy((previous) => ({
                      ...previous,
                      allow_journal_text: event.target.checked,
                    }))
                  }
                />
                <span>{t('guardian.policy.allow_journal_text')}</span>
              </label>
              <label className="row" style={{ cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  style={{ width: 'auto' }}
                  checked={policy.allow_detail}
                  onChange={(event) =>
                    setPolicy((previous) => ({ ...previous, allow_detail: event.target.checked }))
                  }
                />
                <span>{t('guardian.policy.allow_detail')}</span>
              </label>
              <p className="muted">{t('transfer.policy.note')}</p>
            </>
          )}

          {error && (
            <div className="notice error" style={{ marginTop: 'var(--space-3)' }}>
              {error}
            </div>
          )}

          <div className="row" style={{ marginTop: 'var(--space-3)' }}>
            <button className="primary" onClick={goConfirm}>
              {t('transfer.next')}
            </button>
            <button className="ghost" onClick={() => setStep('action')}>
              {t('transfer.back')}
            </button>
          </div>
        </>
      )}

      {step === 'confirm' && action && (
        <>
          <div className="notice warn">{t('transfer.confirm.hint')}</div>
          <div className="section-title">{actionLabel(action)}</div>
          {action === 'policy' && (
            <div className="detail">
              {policy.visible_domains.map(domainLabel).join('、') || '—'}
            </div>
          )}
          <div className="row" style={{ marginTop: 'var(--space-3)' }}>
            <button className="primary" disabled={busy} onClick={() => void submit()}>
              {t('transfer.confirm.action')}
            </button>
            <button className="ghost" disabled={busy} onClick={() => setStep('detail')}>
              {t('transfer.back')}
            </button>
          </div>
        </>
      )}

      {step === 'result' && (
        <>
          <div className="notice ok">{resultText}</div>
          <div className="row" style={{ marginTop: 'var(--space-3)' }}>
            <button
              onClick={() => {
                setAction(null)
                setGuardianPassphrase('')
                setStep('action')
              }}
            >
              {t('transfer.again')}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
