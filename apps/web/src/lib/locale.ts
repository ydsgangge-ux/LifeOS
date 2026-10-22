/** 语言切换策略（P6 W4）：界面**先**切，档案**后**同步。
 *
 * 顺序很重要：先 `setLocale`（i18next 立刻换词表、写 localStorage），让用户马上看到
 * 效果；再调后端把选择记进 `profile_meta.locale`，下次解锁沿用。
 * 同步失败**不阻断**——本次界面已经切好了，只提示「下次可能回到原语言」。
 */

import { availableLocales, setLocale } from '../i18n'
import { api } from './api'

/** 语言自称。**不翻译**：在英文界面里也该看到「简体中文」，方便找到自己的语言。 */
const NATIVE_NAMES: Record<string, string> = {
  'zh-CN': '简体中文',
  'zh-TW': '繁體中文',
  en: 'English',
  ja: '日本語',
  es: 'Español',
}

export interface LocaleOption {
  code: string
  label: string
}

/** 语言选项：有多少份词表就有多少项（新增语言只需往 i18n/ 放文件，这里不用改）。 */
export function localeOptions(): LocaleOption[] {
  return availableLocales.map((code) => ({ code, label: localeLabel(code) }))
}

export function localeLabel(code: string): string {
  return NATIVE_NAMES[code] ?? code
}

/** 同步结果：ok=已落档案；skipped=只切了界面（未解锁或没有该词表）；failed=同步失败。 */
export type LocaleSync = 'ok' | 'skipped' | 'failed'

export interface LocaleChangeResult {
  locale: string
  sync: LocaleSync
}

/** 切语言。`profileId` 为空（还没解锁）或词表不存在时只切界面，不算失败。 */
export async function changeLocale(
  locale: string,
  profileId: string | null,
): Promise<LocaleChangeResult> {
  setLocale(locale)
  if (!profileId || !availableLocales.includes(locale)) {
    return { locale, sync: 'skipped' }
  }
  try {
    await api.setProfileLocale(profileId, locale)
    return { locale, sync: 'ok' }
  } catch {
    return { locale, sync: 'failed' }
  }
}
