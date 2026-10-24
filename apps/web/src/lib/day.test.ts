import { describe, expect, it } from 'vitest'

import { logicalDate, logicalToday } from './day'

/** 用本地时间构造，避免测试机时区影响。 */
function at(month: number, day: number, hour: number, minute = 0): Date {
  return new Date(2026, month - 1, day, hour, minute)
}

describe('logicalDate', () => {
  it('凌晨 0–4 点算前一天', () => {
    expect(logicalDate(0, at(10, 25, 0, 0))).toBe('2026-10-24')
    expect(logicalDate(0, at(10, 25, 3, 59))).toBe('2026-10-24')
  })

  it('04:00 起算当天', () => {
    expect(logicalDate(0, at(10, 25, 4, 0))).toBe('2026-10-25')
  })

  it('白天不变', () => {
    expect(logicalDate(0, at(10, 25, 12, 0))).toBe('2026-10-25')
  })

  it('偏移天数在逻辑日上减', () => {
    expect(logicalDate(7, at(10, 25, 1, 0))).toBe('2026-10-17')
    expect(logicalDate(-30, at(10, 25, 1, 0))).toBe('2026-11-23')
  })

  it('跨月/跨年按逻辑日算', () => {
    expect(logicalDate(0, at(11, 1, 0, 30))).toBe('2026-10-31')
    expect(logicalDate(0, at(1, 1, 2, 0))).toBe('2025-12-31')
  })
})

describe('logicalToday', () => {
  it('返回逻辑日的零点', () => {
    const day = logicalToday(at(11, 1, 0, 30))
    expect([day.getFullYear(), day.getMonth() + 1, day.getDate()]).toEqual([2026, 10, 31])
    expect([day.getHours(), day.getMinutes(), day.getSeconds()]).toEqual([0, 0, 0])
  })
})
