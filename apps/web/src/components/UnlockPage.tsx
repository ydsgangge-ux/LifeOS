/** 解锁页（附录 A 周末任务之一）。
 *
 * 档案未解锁时，除 /profiles 与 /health 外一律 423（第 12.1 节），
 * 所以这里是唯一能在锁定态下操作数据的地方：列档案、创建、解锁。
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import { ageBandLabel } from '../lib/labels'
import { getToken, setToken } from '../lib/session'
import { ApiError, type ProfileCreated, type ProfileSummary, type UnlockResult } from '../lib/types'

const AGE_BANDS = ['child', 'teen', 'adult', 'senior']

interface Props {
  onUnlocked: (result: UnlockResult) => void
  onTokenChanged: () => void
  hasToken: boolean
}

export function UnlockPage({ onUnlocked, onTokenChanged, hasToken }: Props) {
  const { t } = useTranslation()
  const [profiles, setProfiles] = useState<ProfileSummary[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const [tokenDraft, setTokenDraft] = useState('')

  const [unlockingId, setUnlockingId] = useState<string | null>(null)
  const [credential, setCredential] = useState('')
  const [useRecoveryKey, setUseRecoveryKey] = useState(false)
  // 用监护人口令解锁（第 7.7 节）：会话角色变成 guardian，读路径按可见范围过滤。
  const [guardianMode, setGuardianMode] = useState(false)

  const [creating, setCreating] = useState(false)
  const [alias, setAlias] = useState('')
  const [ageBand, setAgeBand] = useState('')
  const [birthYear, setBirthYear] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [repeat, setRepeat] = useState('')
  // 儿童档（第 7.7 节）：必须勾选监护人同意；监护人口令可选（挂了就能以监护人身份解锁）。
  const [guardianConsent, setGuardianConsent] = useState(false)
  const [guardianPassphrase, setGuardianPassphrase] = useState('')

  const [created, setCreated] = useState<ProfileCreated | null>(null)

  const loadProfiles = async () => {
    try {
      const result = await api.listProfiles()
      setProfiles(result.items)
      setError('')
    } catch (problem) {
      setError(problem instanceof ApiError ? problem.message : t('unlock.load_profiles_failed'))
    }
  }

  useEffect(() => {
    if (hasToken) {
      void loadProfiles()
    }
  }, [hasToken])

  const handleSaveToken = () => {
    setToken(tokenDraft)
    setTokenDraft('')
    onTokenChanged()
  }

  const handleUnlock = async (profile: ProfileSummary) => {
    if (!credential) {
      setError(t('unlock.need_credential'))
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await api.unlock(
        profile.id,
        useRecoveryKey
          ? { recovery_key: credential }
          : { passphrase: credential, role: guardianMode ? 'guardian' : 'owner' },
      )
      setCredential('')
      setUnlockingId(null)
      setGuardianMode(false)
      onUnlocked(result)
    } catch (problem) {
      setError(problem instanceof ApiError ? problem.message : t('unlock.failed'))
    } finally {
      setBusy(false)
    }
  }

  const handleCreate = async () => {
    if (!alias.trim()) {
      setError(t('unlock.alias_required'))
      return
    }
    if (!ageBand) {
      setError(t('unlock.age_band_required'))
      return
    }
    if (ageBand === 'child' && !guardianConsent) {
      setError(t('guardian.consent_required'))
      return
    }
    if (passphrase.length < 8) {
      setError(t('unlock.passphrase_too_short'))
      return
    }
    if (passphrase !== repeat) {
      setError(t('unlock.passphrase_mismatch'))
      return
    }
    if (guardianPassphrase && guardianPassphrase.length < 6) {
      setError(t('unlock.guardian_passphrase_too_short'))
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await api.createProfile({
        alias: alias.trim(),
        passphrase,
        age_band: ageBand,
        birth_year: birthYear ? Number(birthYear) : null,
        guardian_consent: guardianConsent,
        guardian_passphrase: guardianPassphrase || null,
      })
      setCreated(result)
      setCreating(false)
      setAlias('')
      setAgeBand('')
      setBirthYear('')
      setPassphrase('')
      setRepeat('')
      setGuardianConsent(false)
      setGuardianPassphrase('')
      await loadProfiles()
    } catch (problem) {
      setError(problem instanceof ApiError ? problem.message : t('unlock.create_failed'))
    } finally {
      setBusy(false)
    }
  }

  if (!hasToken) {
    return (
      <div className="gate">
        <div className="hero">
          <h1>{t('app.brand')}</h1>
          <p>{t('unlock.hero_sub')}</p>
        </div>
        <div className="card">
          <h2>{t('unlock.paste_token_title')}</h2>
          <p className="muted">{t('unlock.paste_token_body')}</p>
          <div className="field">
            <label>{t('unlock.session_token')}</label>
            <input
              type="text"
              value={tokenDraft}
              onChange={(event) => setTokenDraft(event.target.value)}
              placeholder={t('unlock.token_placeholder')}
            />
          </div>
          <button className="primary" onClick={handleSaveToken} disabled={!tokenDraft.trim()}>
            {t('unlock.use_token')}
          </button>
        </div>
      </div>
    )
  }

  if (created) {
    return (
      <div className="gate">
        <div className="card">
          <h2>{t('unlock.created_title', { alias: created.alias })}</h2>
          <div className="notice warn">{created.notice}</div>
          <div className="recovery-key" data-testid="recovery-key">
            {created.recovery_key}
          </div>
          <p className="muted">{t('unlock.recovery_once')}</p>
          <div className="row">
            <button
              className="primary"
              onClick={() => {
                void navigator.clipboard?.writeText(created.recovery_key)
              }}
            >
              {t('unlock.copy')}
            </button>
            <button
              onClick={() => {
                setCreated(null)
                void loadProfiles()
              }}
            >
              {t('unlock.saved_return')}
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="gate">
      <div className="hero">
        <h1>{t('app.brand')}</h1>
        <p>{t('unlock.hero_pick')}</p>
      </div>

      {error && (
        <div className="notice error" style={{ marginBottom: 'var(--space-3)' }}>
          {error}
        </div>
      )}

      {profiles.length === 0 && !creating && (
        <div className="card">
          <p className="muted">{t('unlock.no_profiles')}</p>
        </div>
      )}

      {profiles.map((profile) => (
        <div className="card" key={profile.id}>
          <div className="row">
            <div>
              <div className="name">{profile.alias}</div>
              <div className="meta">
                {t('unlock.created_at', { date: profile.created_at.slice(0, 10) })}
              </div>
            </div>
            <div className="spacer" style={{ flex: 1 }} />
            {unlockingId === profile.id ? (
              <button className="ghost" onClick={() => setUnlockingId(null)}>
                {t('unlock.cancel')}
              </button>
            ) : (
              <button className="primary" onClick={() => setUnlockingId(profile.id)}>
                {t('unlock.unlock')}
              </button>
            )}
          </div>

          {unlockingId === profile.id && (
            <div style={{ marginTop: 'var(--space-3)' }}>
              <div className="field">
                <label>
                  {useRecoveryKey
                    ? t('unlock.recovery_key')
                    : guardianMode
                      ? t('unlock.guardian_passphrase_label')
                      : t('unlock.passphrase')}
                </label>
                <input
                  type={useRecoveryKey ? 'text' : 'password'}
                  data-testid="unlock-credential"
                  value={credential}
                  autoFocus
                  onChange={(event) => setCredential(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') void handleUnlock(profile)
                  }}
                />
              </div>
              <div className="row">
                <button className="primary" onClick={() => void handleUnlock(profile)} disabled={busy}>
                  {t('unlock.unlock')}
                </button>
                <button
                  className="ghost"
                  onClick={() => {
                    setUseRecoveryKey(!useRecoveryKey)
                    setGuardianMode(false)
                    setCredential('')
                  }}
                >
                  {useRecoveryKey ? t('unlock.switch_to_passphrase') : t('unlock.switch_to_recovery')}
                </button>
              </div>
              {!useRecoveryKey && (
                <label className="row" style={{ cursor: 'pointer', marginTop: 'var(--space-2)' }}>
                  <input
                    type="checkbox"
                    style={{ width: 'auto' }}
                    checked={guardianMode}
                    onChange={(event) => {
                      setGuardianMode(event.target.checked)
                      setCredential('')
                    }}
                  />
                  <span>{t('unlock.guardian_mode')}</span>
                </label>
              )}
            </div>
          )}
        </div>
      ))}

      <div className="card">
        {creating ? (
          <>
            <h2>{t('unlock.new_profile')}</h2>
            <div className="field">
              <label>{t('unlock.alias_label')}</label>
              <input
                type="text"
                data-testid="new-alias"
                value={alias}
                onChange={(event) => setAlias(event.target.value)}
              />
            </div>
            <div className="grid-2">
              <div>
                <label>{t('unlock.age_band')}</label>
                <select
                  data-testid="new-age-band"
                  value={ageBand}
                  onChange={(event) => setAgeBand(event.target.value)}
                >
                  <option value="">{t('unlock.choose')}</option>
                  {AGE_BANDS.map((band) => (
                    <option key={band} value={band}>
                      {ageBandLabel(band)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label>{t('unlock.birth_year')}</label>
                <input
                  type="text"
                  value={birthYear}
                  onChange={(event) => setBirthYear(event.target.value)}
                />
              </div>
            </div>
            {ageBand === 'child' && (
              <>
                <div className="notice warn" style={{ marginBottom: 'var(--space-3)' }}>
                  {t('guardian.viewer.limit')}
                </div>
                <div className="field">
                  <label className="row" style={{ cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      style={{ width: 'auto' }}
                      checked={guardianConsent}
                      onChange={(event) => setGuardianConsent(event.target.checked)}
                    />
                    <span>{t('guardian.consent_label')}</span>
                  </label>
                </div>
                <div className="field">
                  <label>{t('unlock.guardian_passphrase')}</label>
                  <input
                    type="password"
                    value={guardianPassphrase}
                    onChange={(event) => setGuardianPassphrase(event.target.value)}
                  />
                  <p className="muted">{t('unlock.guardian_passphrase_hint')}</p>
                </div>
              </>
            )}
            <div className="grid-2">
              <div>
                <label>{t('unlock.passphrase_min')}</label>
                <input
                  type="password"
                  data-testid="new-passphrase"
                  value={passphrase}
                  onChange={(event) => setPassphrase(event.target.value)}
                />
              </div>
              <div>
                <label>{t('unlock.repeat_passphrase')}</label>
                <input
                  type="password"
                  data-testid="new-repeat"
                  value={repeat}
                  onChange={(event) => setRepeat(event.target.value)}
                />
              </div>
            </div>
            <p className="muted">{t('unlock.key_hint')}</p>
            <div className="row">
              <button className="primary" onClick={() => void handleCreate()} disabled={busy}>
                {t('unlock.create')}
              </button>
              <button className="ghost" onClick={() => setCreating(false)}>
                {t('unlock.cancel')}
              </button>
            </div>
          </>
        ) : (
          <div className="row">
            <button onClick={() => setCreating(true)}>{t('unlock.new_profile')}</button>
            <span className="muted">
              {getToken() ? t('unlock.connected', { token: getToken()?.slice(0, 6) ?? '' }) : ''}
            </span>
          </div>
        )}
      </div>
    </div>
  )
}
