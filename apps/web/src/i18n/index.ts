/** 前端国际化（NFR-08）：词表与后端同源。
 *
 * 词表直接读仓库根的 i18n/<locale>.yaml（做法同 schemas/dictionaries），
 * 由 Vite 打进产物，因此打包后同源托管也能用，不需要额外的接口。
 *
 * 约定与后端 lifeos.i18n 保持一致：
 *   - 键是「平键点号」，整串就是一个键（keySeparator / nsSeparator 关掉）；
 *   - 占位符统一 {name}（把 i18next 的前后缀调到同一对花括号）。
 *
 * 数据 / Schema 里的文案写成 `i18n:<key>` 引用，用 resolveI18nRef() 解析。
 * 新增语言：在仓库根 i18n/ 放 <locale>.yaml 即可，代码不用改。
 */

import i18next from 'i18next'
import { load } from 'js-yaml'
import { initReactI18next } from 'react-i18next'

export const DEFAULT_LOCALE = 'zh-CN'

const modules = import.meta.glob('../../../../i18n/*.yaml', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

function localeOf(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1).replace(/\.yaml$/, '')
}

const resources: Record<string, { translation: Record<string, string> }> = {}
for (const [path, text] of Object.entries(modules)) {
  const catalog = (load(text) ?? {}) as Record<string, unknown>
  const translation: Record<string, string> = {}
  for (const [key, value] of Object.entries(catalog)) {
    translation[key] = String(value)
  }
  resources[localeOf(path)] = { translation }
}

/** 当前可用的语言：i18n/ 里有几个词表文件就有几种。 */
export const availableLocales = Object.keys(resources).sort()

const STORAGE_KEY = 'lifeos.locale'

function readStoredLocale(): string | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

// initReactI18next 把 React 与这个 i18next 实例接上：少了它，useTranslation()
// 拿不到实例，界面会直接把 key 显示出来（而不是报错，很容易漏掉）。
i18next.use(initReactI18next).init({
  lng: readStoredLocale() ?? DEFAULT_LOCALE,
  fallbackLng: DEFAULT_LOCALE,
  supportedLngs: availableLocales,
  resources,
  keySeparator: false,
  nsSeparator: false,
  interpolation: { prefix: '{', suffix: '}', escapeValue: false },
  initAsync: false,
  returnNull: false,
  react: { useSuspense: false },
})

/** 非组件场景取词用；组件里请用 useTranslation()，语言切换才会重渲染。 */
export const t = i18next.t.bind(i18next)

export function getLocale(): string {
  return i18next.resolvedLanguage ?? DEFAULT_LOCALE
}

/** 切换语言：记住选择，并让 useTranslation 订阅的组件重渲染。 */
export function setLocale(locale: string): void {
  if (!resources[locale] || locale === getLocale()) return
  try {
    localStorage.setItem(STORAGE_KEY, locale)
  } catch {
    // 存不进去（隐私模式等）不影响本次切换
  }
  void i18next.changeLanguage(locale)
}

/** 解析数据层文案引用：`i18n:form.daily.title` → 当前语言的文案。
 *
 * 不带 `i18n:` 前缀的原样返回（兼容字典里还没键化的纯文本）；查不到就退回
 * key 本身，方便一眼看出遗漏。
 */
export function resolveI18nRef(raw: string): string {
  if (!raw.startsWith('i18n:')) return raw
  const key = raw.slice('i18n:'.length)
  return t(key, { defaultValue: key })
}
