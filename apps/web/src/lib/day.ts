/**
 * 逻辑日（与后端 lifeos/util/ids.py 的 LOGICAL_DAY_CUTOFF_HOUR 保持一致）。
 *
 * 用户常在 0 点后才整理刚过完的一天：凌晨 0–4 点仍算前一天。
 * 界面上的「今天」、最近 N 天窗口都必须按这个口径算，否则会与后端聚合、
 * 周报/月报的周期边界错开一天。
 */

export const DAY_CUTOFF_HOUR = 4

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** 逻辑日的 YYYY-MM-DD；offsetDays 表示在逻辑日上再往前推几天。 */
export function logicalDate(offsetDays = 0, now: Date = new Date()): string {
  const date = new Date(now.getTime())
  date.setHours(date.getHours() - DAY_CUTOFF_HOUR)
  date.setDate(date.getDate() - offsetDays)
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 逻辑日的当天零点（用于算「距今几天」）。 */
export function logicalToday(now: Date = new Date()): Date {
  const date = new Date(now.getTime())
  date.setHours(date.getHours() - DAY_CUTOFF_HOUR)
  date.setHours(0, 0, 0, 0)
  return date
}
