/** 外观与主题（第 11.3 节、第 11.1 节设置页）。
 *
 * 上传一张图 → 本地取色（图片不出站）→ 实时预览亮暗两套 → 保存时才把
 * token 与原图（加密附件）写进档案。预览只改 CSS 变量，取消即还原。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import type { ThemeRecord } from '../lib/types'
import {
  applyTheme,
  loadStoredTheme,
  prefersDark,
  saveStoredTheme,
  type Scheme,
  type StoredTheme,
} from '../theme/apply'
import { themeFromFile } from '../theme/engine'
import { neutralTheme, type ThemeTokens } from '../theme/palette'

type Mode = 'system' | 'light' | 'dark'

/** 预览用的色块：三组语义色 + 六色图表板。 */
const SWATCHES = [
  '--primary',
  '--accent',
  '--success',
  '--warning',
  '--danger',
  '--chart-1',
  '--chart-2',
  '--chart-3',
  '--chart-4',
  '--chart-5',
  '--chart-6',
] as const

function initialMode(): Mode {
  const stored = loadStoredTheme()
  return stored.tokens ? stored.scheme : 'system'
}

interface Props {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}

export function ThemeEditor({ onError, onNotice }: Props) {
  const { t } = useTranslation()
  const [base, setBase] = useState<StoredTheme>(() => loadStoredTheme())
  const [previewTokens, setPreviewTokens] = useState<ThemeTokens | null>(null)
  const [mode, setMode] = useState<Mode>(initialMode)
  const [systemDark, setSystemDark] = useState(prefersDark)
  const [name, setName] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [generating, setGenerating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [themes, setThemes] = useState<ThemeRecord[]>([])
  const [loadingThemes, setLoadingThemes] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const scheme: Scheme = mode === 'system' ? (systemDark ? 'dark' : 'light') : mode

  // 跟随系统：OS 配色变化时实时跟上（第 11.2 节）。
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    query.addEventListener('change', handler)
    return () => query.removeEventListener('change', handler)
  }, [])

  // 预览与正式主题都只落到 CSS 变量上。
  useEffect(() => {
    applyTheme({
      scheme,
      tokens: previewTokens ?? base.tokens,
      themeId: base.themeId,
      name: base.name,
    })
  }, [scheme, previewTokens, base])

  const loadThemes = useCallback(async () => {
    setLoadingThemes(true)
    try {
      const list = await api.listThemes()
      setThemes(list.items)
    } catch (problem) {
      onError(problem, t('theme.load_themes_failed'))
    } finally {
      setLoadingThemes(false)
    }
  }, [onError, t])

  useEffect(() => {
    void loadThemes()
  }, [loadThemes])

  const neutral = useMemo(() => neutralTheme(), [])
  const usedFallback =
    previewTokens !== null && JSON.stringify(previewTokens) === JSON.stringify(neutral)

  const currentRecord = themes.find((record) => record.id === base.themeId) ?? null
  const displayTokens = previewTokens ?? base.tokens
  const swatchBlock = displayTokens?.[scheme] ?? null

  const pickFile = async (chosen: File | null) => {
    if (!chosen) return
    if (!chosen.type.startsWith('image/')) {
      onError(new Error('not_image'), t('theme.not_image'))
      return
    }
    setFile(chosen)
    setGenerating(true)
    try {
      setPreviewTokens(await themeFromFile(chosen))
    } catch (problem) {
      onError(problem, t('theme.failed'))
    } finally {
      setGenerating(false)
    }
  }

  const cancelPreview = () => {
    setPreviewTokens(null)
    setFile(null)
    setName('')
  }

  const save = async () => {
    if (!previewTokens) return
    const themeName = name.trim()
    if (!themeName) return
    setSaving(true)
    try {
      let sourceId: string | null = null
      if (file) {
        const attachment = await api.uploadAttachment(file, 'image')
        sourceId = attachment.id
      }
      const record = await api.createTheme({
        name: themeName,
        tokens: previewTokens,
        source_attachment_id: sourceId,
      })
      const next: StoredTheme = {
        scheme,
        tokens: record.tokens,
        themeId: record.id,
        name: record.name,
      }
      setBase(next)
      saveStoredTheme(next)
      setPreviewTokens(null)
      setFile(null)
      setName('')
      onNotice(t('theme.saved'))
      await loadThemes()
    } catch (problem) {
      onError(problem, t('theme.save_failed'))
    } finally {
      setSaving(false)
    }
  }

  const activate = async (record: ThemeRecord) => {
    try {
      const active = await api.activateTheme(record.id)
      const next: StoredTheme = {
        scheme,
        tokens: active.tokens,
        themeId: active.id,
        name: active.name,
      }
      setBase(next)
      saveStoredTheme(next)
      setPreviewTokens(null)
      setName('')
      onNotice(t('theme.activate_ok'))
      await loadThemes()
    } catch (problem) {
      onError(problem, t('theme.load_themes_failed'))
    }
  }

  const reset = () => {
    const next: StoredTheme = { scheme, tokens: null, themeId: null, name: null }
    setBase(next)
    saveStoredTheme(next)
    setPreviewTokens(null)
    setName('')
    onNotice(t('theme.reset_ok'))
  }

  return (
    <div className="content single">
      <div className="card">
        <h2>{t('theme.title')}</h2>
        <p className="muted">{t('theme.hint')}</p>

        <div className="row wrap" style={{ marginTop: 'var(--space-3)' }}>
          <span className="muted">{t('theme.current')}</span>
          <span className="pill">{base.name ?? t('theme.current_default')}</span>
          {currentRecord?.source_attachment_id && (
            <span className="muted">{t('theme.source_image')}</span>
          )}
        </div>

        <div className="row wrap" style={{ marginTop: 'var(--space-3)' }}>
          <label htmlFor="theme-mode" style={{ margin: 0 }}>
            {t('theme.mode')}
          </label>
          <select
            id="theme-mode"
            value={mode}
            onChange={(event) => setMode(event.target.value as Mode)}
            style={{ width: 'auto' }}
          >
            <option value="system">{t('theme.mode_system')}</option>
            <option value="light">{t('theme.mode_light')}</option>
            <option value="dark">{t('theme.mode_dark')}</option>
          </select>
          <button className="ghost" onClick={reset}>
            {t('theme.reset')}
          </button>
        </div>
      </div>

      <div className="card">
        <h2>{t('theme.preview')}</h2>

        <div className="field">
          <label htmlFor="theme-name">{t('theme.name_label')}</label>
          <input
            id="theme-name"
            value={name}
            placeholder={t('theme.name_placeholder')}
            onChange={(event) => setName(event.target.value)}
          />
        </div>

        <div className="row wrap">
          <button onClick={() => fileInput.current?.click()}>{t('theme.upload')}</button>
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            style={{ display: 'none' }}
            onChange={(event) => void pickFile(event.target.files?.[0] ?? null)}
          />
          <button onClick={() => setMode('light')}>{t('theme.apply_light')}</button>
          <button onClick={() => setMode('dark')}>{t('theme.apply_dark')}</button>
          {previewTokens && (
            <button className="ghost" onClick={cancelPreview}>
              {t('theme.cancel')}
            </button>
          )}
        </div>

        {generating && <p className="muted">{t('theme.generating')}</p>}
        {!generating && previewTokens && <p className="muted">{t('theme.generated')}</p>}
        {usedFallback && <div className="notice warn">{t('theme.neutral_fallback')}</div>}

        {swatchBlock && (
          <>
            <h3>{t('theme.swatches')}</h3>
            <div className="theme-swatches">
              {SWATCHES.map((key) => (
                <span
                  className="swatch"
                  key={key}
                  title={key}
                  style={{ background: swatchBlock[key] }}
                />
              ))}
            </div>
          </>
        )}

        {previewTokens && (
          <div className="row" style={{ marginTop: 'var(--space-3)' }}>
            <button
              className="primary"
              disabled={saving || !name.trim()}
              onClick={() => void save()}
            >
              {saving ? t('theme.saving') : t('theme.save')}
            </button>
          </div>
        )}
      </div>

      <div className="card">
        <h2>{t('theme.saved_themes')}</h2>
        {loadingThemes && <p className="muted">{t('entry.loading')}</p>}
        {!loadingThemes && themes.length === 0 && (
          <p className="muted">{t('theme.themes_empty')}</p>
        )}
        {themes.map((record) => {
          const active = record.id === base.themeId
          return (
            <div className="theme-preview" key={record.id}>
              <div className="preview-title">
                {record.name}
                {active && <span className="tag">{t('theme.current')}</span>}
              </div>
              <div className="theme-swatches">
                {SWATCHES.map((key) => (
                  <span
                    className="swatch"
                    key={key}
                    title={key}
                    style={{ background: record.tokens[scheme][key] }}
                  />
                ))}
              </div>
              <div className="preview-muted">
                {record.source_attachment_id ? t('theme.source_image') : t('theme.source_none')}
              </div>
              <div className="row">
                <button disabled={active} onClick={() => void activate(record)}>
                  {t('theme.activate')}
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
