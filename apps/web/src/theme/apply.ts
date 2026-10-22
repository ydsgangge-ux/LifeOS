/** 主题的应用与持久化（第 11.3 节第 11 步：预览只改 CSS 变量，实时生效）。
 *
 * 主题存在浏览器本地（localStorage）：取色与 token 全在本地算，token 也存本地，
 * 同步一份到加密档案是为了换设备能带走，不参与渲染。
 */

import type { ThemeTokens } from './palette'

export type Scheme = 'light' | 'dark'

/** 主题引擎会覆盖的颜色变量（第 11.3 节 token 清单里的颜色部分）。 */
export const COLOR_KEYS = [
  '--bg',
  '--surface',
  '--surface-2',
  '--text',
  '--text-muted',
  '--border',
  '--border-strong',
  '--primary',
  '--on-primary',
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

export interface StoredTheme {
  scheme: Scheme
  /** 自定义 token；为 null 表示用 theme.css 里的默认亮/暗 set。 */
  tokens: ThemeTokens | null
  themeId: string | null
  name: string | null
}

const STORAGE_KEY = 'lifeos.theme'

export function prefersDark(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  )
}

export function loadStoredTheme(): StoredTheme {
  const fallback: StoredTheme = {
    scheme: prefersDark() ? 'dark' : 'light',
    tokens: null,
    themeId: null,
    name: null,
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return fallback
    const parsed = JSON.parse(raw) as Partial<StoredTheme>
    return {
      scheme: parsed.scheme === 'dark' ? 'dark' : parsed.scheme === 'light' ? 'light' : fallback.scheme,
      tokens: parsed.tokens ?? null,
      themeId: parsed.themeId ?? null,
      name: parsed.name ?? null,
    }
  } catch {
    return fallback
  }
}

export function saveStoredTheme(theme: StoredTheme): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(theme))
  } catch {
    // 隐私模式等存不进去不影响本次效果
  }
}

/** 应用主题：把 token 写到 <html> 行内变量；tokens 为空则清空回退到 CSS 默认。 */
export function applyTheme(theme: StoredTheme): void {
  const root = document.documentElement
  root.dataset.scheme = theme.scheme
  for (const key of COLOR_KEYS) root.style.removeProperty(key)
  if (!theme.tokens) return
  const block = theme.tokens[theme.scheme]
  for (const key of COLOR_KEYS) {
    const value = block[key]
    if (value) root.style.setProperty(key, value)
  }
}

/** 年龄模式：字号与控件密度挂到根属性上，由 theme.css 的规则接管（第 11.2 节）。 */
export function applyAgeBand(ageBand: string | null): void {
  const root = document.documentElement
  if (ageBand) root.dataset.ageBand = ageBand
  else delete root.dataset.ageBand
}

/** 信息密度：舒适 / 紧凑（第 11.2 节）。 */
export function applyDensity(density: 'comfortable' | 'compact'): void {
  document.documentElement.dataset.density = density
}
