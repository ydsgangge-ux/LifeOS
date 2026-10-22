/** 日期与数字的本地化格式（NFR-08、P6 W5）。
 *
 * 格式跟着界面语言走：日期用 Intl.DateTimeFormat，数字与金额用 Intl.NumberFormat。
 * 语言码取自 i18n（`getLocale()`，默认 zh-CN）；「en」「ja」「es」这类短码 Intl 也认。
 */

import { getLocale } from '../i18n'

/** 把**纯日期** `YYYY-MM-DD` 按本地时区解析成当天零点。
 *
 * 直接 `new Date('2026-10-20')` 会当成 UTC 零点，在西半球时区会退回前一天，
 * 所以这里显式按年月日构造；带时刻的字符串（`...T09:30:00`）交给 Date 自己解析。
 */
const ISO_DATE_ONLY_RE = /^(\d{4})-(\d{2})-(\d{2})$/

function toDate(value: string | Date): Date | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value
  const match = ISO_DATE_ONLY_RE.exec(value)
  const date = match
    ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
    : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/** 日期：2026-10-20 → 「2026年10月20日」/「October 20, 2026」…… 解析不出来就原样返回。 */
export function formatDate(
  value: string | Date,
  options: Intl.DateTimeFormatOptions = {},
): string {
  const date = toDate(value)
  if (!date) return typeof value === 'string' ? value : ''
  return new Intl.DateTimeFormat(getLocale(), {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    ...options,
  }).format(date)
}

/** 日期 + 时间：用于「生成于 / 缓存于」这类带时刻的时间戳。 */
export function formatDateTime(
  value: string | Date,
  options: Intl.DateTimeFormatOptions = {},
): string {
  const date = toDate(value)
  if (!date) return typeof value === 'string' ? value : ''
  return new Intl.DateTimeFormat(getLocale(), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    ...options,
  }).format(date)
}

/** 数字：按语言决定千分位与小数点；没值或不是数字就返回空串。 */
export function formatNumber(
  value: number | null | undefined,
  options: Intl.NumberFormatOptions = {},
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return ''
  return new Intl.NumberFormat(getLocale(), options).format(value)
}

/** 金额：入参是最小币种单位（如「分」），按 `minorExp` 还原成小数后再按币种格式化。
 *
 * 币种码不被 Intl 认识时退回纯数字，不让一处不认识的币种把整个界面打崩。
 */
export function formatAmount(
  value: number | null | undefined,
  currency = 'CNY',
  minorExp = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return ''
  const amount = value / 10 ** minorExp
  try {
    return new Intl.NumberFormat(getLocale(), {
      style: 'currency',
      currency,
    }).format(amount)
  } catch {
    return formatNumber(amount)
  }
}
