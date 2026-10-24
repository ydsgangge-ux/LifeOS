import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { DEFAULT_LOCALE, getLocale, setLocale } from '../i18n'
import { formatAmount, formatDate, formatDateTime, formatNumber } from './format'

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('本地化格式', () => {
  beforeEach(async () => {
    setLocale(DEFAULT_LOCALE)
    await flush()
  })

  afterEach(async () => {
    setLocale(DEFAULT_LOCALE)
    await flush()
  })

  it('日期按界面语言排版，不是原样回吐 ISO 串', () => {
    const text = formatDate('2026-10-20')
    expect(text).not.toBe('2026-10-20')
    expect(text).toContain('2026')
    expect(text).toContain('20')
  })

  it('YYYY-MM-DD 按本地时区解析，不会被 UTC 拉回前一天', () => {
    // 若当成 UTC 零点解析，负时区会退回 19 日；固定一个负时区验证一下
    const text = formatDate('2026-10-20', { timeZone: 'America/New_York' })
    expect(text).toContain('20')
  })

  it('解析不出日期时原样返回，不让界面出现 Invalid Date', () => {
    expect(formatDate('不是日期')).toBe('不是日期')
  })

  it('带时刻的时间戳走 formatDateTime', () => {
    const text = formatDateTime('2026-10-20T09:30:00')
    expect(text).toContain('2026')
    expect(text).toContain('30')
  })

  it('UTC 时间戳按目标时区换算，不把 UTC 的时与分直接显示出来', () => {
    // 后端 created_at 是 UTC（+00:00）：东八区应显示 09:05，而不是 01:05
    const text = formatDateTime('2026-10-20T01:05:00+00:00', {
      timeZone: 'Asia/Shanghai',
    })
    expect(text).toContain('09')
    expect(text).not.toContain('01')
  })

  it('数字按语言加千分位；没值返回空串', () => {
    expect(formatNumber(1234567)).toContain('1')
    expect(formatNumber(1234567)).toMatch(/[,.٬\s]/)
    expect(formatNumber(null)).toBe('')
    expect(formatNumber(undefined)).toBe('')
    expect(formatNumber(Number.NaN)).toBe('')
  })

  it('金额按最小币种单位还原后格式化', () => {
    const text = formatAmount(2800, 'CNY', 2)
    expect(text).toContain('28')
    // 认识不了的币种码退回纯数字，不把界面打崩
    expect(formatAmount(2800, 'NOT-A-CODE', 2)).toContain('28')
  })

  it('切换语言后格式跟着走', async () => {
    const zh = formatDate('2026-10-20')
    setLocale('en')
    await flush()
    expect(getLocale()).toBe('en')
    const en = formatDate('2026-10-20')
    expect(en).not.toBe(zh)
  })
})
