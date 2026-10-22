/** 模型供应商设置（第 9.2 节、第 11.1 节）。
 *
 * 只编辑非敏感项（供应商 / 模型 / 接口地址 / 超时 / 重试）；API 密钥走系统钥匙串，
 * 输入框永远不回填已保存的密钥，只显示「已保存 / 未保存」。留空即保持原密钥，
 * 要删除必须勾选「删除已保存的密钥」。
 *
 * 这里不做联网测试：真实调用必须经过隐私网关留下出站审计，绕开它发试探请求
 * 会破坏「每次出站都有记录」的承诺。
 */

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import type { LlmProviderInfo, LlmSettings as LlmSettingsPayload } from '../lib/types'

interface Props {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}

export function LlmSettings({ onError, onNotice }: Props) {
  const { t } = useTranslation()
  const [info, setInfo] = useState<LlmSettingsPayload | null>(null)
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [customModel, setCustomModel] = useState(false)
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [clearKey, setClearKey] = useState(false)
  const [timeoutSeconds, setTimeoutSeconds] = useState(60)
  const [retries, setRetries] = useState(2)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

  const apply = useCallback((next: LlmSettingsPayload) => {
    const current = next.providers.find((item) => item.id === next.provider)
    const suggestions = current?.models ?? []
    setInfo(next)
    setProvider(next.provider)
    setModel(next.model || suggestions[0] || '')
    setCustomModel(suggestions.length === 0 || !suggestions.includes(next.model))
    setBaseUrl(next.base_url)
    setTimeoutSeconds(next.timeout_seconds)
    setRetries(next.max_retries)
    setApiKey('')
    setClearKey(false)
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      apply(await api.getLlmSettings())
    } catch (problem) {
      onError(problem, t('error.load_entries'))
    } finally {
      setLoading(false)
    }
  }, [apply, onError, t])

  useEffect(() => {
    void load()
  }, [load])

  const selected: LlmProviderInfo | undefined = info?.providers.find(
    (item) => item.id === provider,
  )

  const changeProvider = (nextId: string) => {
    setProvider(nextId)
    const next = info?.providers.find((item) => item.id === nextId)
    if (!next) return
    setBaseUrl(next.default_base_url)
    setModel(next.models[0] ?? '')
    setCustomModel(next.models.length === 0)
  }

  const save = async () => {
    setSaving(true)
    try {
      const next = await api.updateLlmSettings({
        provider,
        model: model.trim(),
        base_url: baseUrl.trim(),
        timeout_seconds: timeoutSeconds,
        max_retries: retries,
        api_key: apiKey.trim() || undefined,
        clear_api_key: clearKey,
      })
      apply(next)
      onNotice(t('llm.saved'))
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setSaving(false)
    }
  }

  const statusClass = info?.status === 'ready' ? 'ok' : 'warn'

  return (
    <div className="content single">
      <div className="card">
        <h2>{t('llm.title')}</h2>
        <p className="muted">{t('llm.hint')}</p>
        {loading && !info && <p className="muted">{t('entry.loading')}</p>}

        {info && (
          <>
            {info.status !== 'ready' && (
              <div className={`notice ${statusClass}`}>
                {t('llm.status_label')}：{t(`llm.status.${info.status}`)}
              </div>
            )}

            <div className="field-block">
              <label htmlFor="llm-provider">{t('llm.provider')}</label>
              <select
                id="llm-provider"
                value={provider}
                onChange={(event) => changeProvider(event.target.value)}
              >
                <option value="">{t('llm.provider.none')}</option>
                {info.providers.map((item) => (
                  <option key={item.id} value={item.id}>
                    {t(item.label_key)}
                  </option>
                ))}
              </select>

              {provider && (
                <>
                  <label htmlFor="llm-model">{t('llm.model')}</label>
                  {(selected?.models.length ?? 0) > 0 && !customModel ? (
                    <select
                      id="llm-model"
                      value={model}
                      onChange={(event) => setModel(event.target.value)}
                    >
                      {(selected?.models ?? []).map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      id="llm-model"
                      value={model}
                      placeholder={t('llm.model_hint')}
                      onChange={(event) => setModel(event.target.value)}
                    />
                  )}
                  {(selected?.models.length ?? 0) > 0 && (
                    <label className="row" style={{ cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        style={{ width: 'auto' }}
                        checked={customModel}
                        onChange={(event) => {
                          setCustomModel(event.target.checked)
                          setModel(
                            event.target.checked ? '' : (selected?.models[0] ?? ''),
                          )
                        }}
                      />
                      <span>{t('llm.model_custom')}</span>
                    </label>
                  )}

                  <label htmlFor="llm-base-url">{t('llm.base_url')}</label>
                  <input
                    id="llm-base-url"
                    value={baseUrl}
                    placeholder={t('llm.base_url_hint')}
                    onChange={(event) => setBaseUrl(event.target.value)}
                  />

                  {selected?.requires_key === false ? (
                    <p className="muted">{t('llm.local_hint')}</p>
                  ) : (
                    <>
                      <label htmlFor="llm-api-key">{t('llm.api_key')}</label>
                      <input
                        id="llm-api-key"
                        type="password"
                        autoComplete="off"
                        value={apiKey}
                        placeholder={
                          info.api_key_set ? t('llm.api_key_keep') : t('llm.api_key_missing')
                        }
                        onChange={(event) => {
                          setApiKey(event.target.value)
                          if (event.target.value) setClearKey(false)
                        }}
                      />
                      <p className="muted">
                        {info.api_key_set
                          ? t('llm.api_key_configured')
                          : t('llm.api_key_missing')}
                      </p>
                      {info.api_key_set && (
                        <label className="row" style={{ cursor: 'pointer' }}>
                          <input
                            type="checkbox"
                            style={{ width: 'auto' }}
                            checked={clearKey}
                            onChange={(event) => {
                              setClearKey(event.target.checked)
                              if (event.target.checked) setApiKey('')
                            }}
                          />
                          <span>{t('llm.clear_key')}</span>
                        </label>
                      )}
                    </>
                  )}

                  <label htmlFor="llm-timeout">{t('llm.timeout')}</label>
                  <input
                    id="llm-timeout"
                    type="number"
                    min={5}
                    max={300}
                    value={timeoutSeconds}
                    onChange={(event) => setTimeoutSeconds(Number(event.target.value))}
                  />

                  <label htmlFor="llm-retries">{t('llm.retries')}</label>
                  <input
                    id="llm-retries"
                    type="number"
                    min={0}
                    max={5}
                    value={retries}
                    onChange={(event) => setRetries(Number(event.target.value))}
                  />
                </>
              )}
            </div>

            <div className="row">
              <button className="primary" disabled={saving} onClick={() => void save()}>
                {t('llm.save')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
