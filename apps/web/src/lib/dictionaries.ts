/** 字典读取（第 6.5 节）：直接读仓库根的 schemas/dictionaries/*.yaml。
 *
 * 前端与后端共用同一份定义（第 6 章），构建时由 Vite 打进产物，
 * 因此打包后同源托管也能用，不需要额外的接口。
 */

import { load } from 'js-yaml'

import { resolveI18nRef } from '../i18n'

interface RawDictionary {
  id?: string
  title?: string
  options?: unknown
}

export interface Option {
  key: string
  label: string
}

const modules = import.meta.glob('../../../../schemas/dictionaries/*.yaml', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const dictionaries = new Map<string, Option[]>()

function normalize(raw: unknown): Option[] {
  if (!Array.isArray(raw)) return []
  const out: Option[] = []
  for (const item of raw) {
    if (typeof item === 'string') {
      out.push({ key: item, label: item })
    } else if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>
      const key = String(record.key ?? '')
      // 字典 label 可以是 `i18n:<key>` 引用；纯文本原样返回（见 resolveI18nRef）。
      out.push({ key, label: resolveI18nRef(String(record.label ?? key)) })
    }
  }
  return out
}

for (const text of Object.values(modules)) {
  const parsed = (load(text) ?? {}) as RawDictionary
  if (parsed.id) {
    dictionaries.set(parsed.id, normalize(parsed.options))
  }
}

/** 解析 `dict:expense_category` 这类引用；未知字典返回空数组。 */
export function dictionaryOptions(ref: string): Option[] {
  if (!ref.startsWith('dict:')) return []
  return dictionaries.get(ref.slice('dict:'.length)) ?? []
}

/** 把表单字段的 options 统一成可渲染的列表；自由数组按「值即标签」处理。 */
export function resolveOptions(options: unknown): Option[] {
  if (typeof options === 'string') {
    return dictionaryOptions(options)
  }
  if (Array.isArray(options)) {
    return normalize(options)
  }
  return []
}
