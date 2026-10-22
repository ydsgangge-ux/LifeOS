import { describe, expect, it } from 'vitest'

import {
  DEFAULT_LOCALE,
  availableLocales,
  getLocale,
  resolveI18nRef,
  setLocale,
  t,
} from './index'

describe('i18n 地基', () => {
  it('载入共享词表，默认语言可用', () => {
    expect(availableLocales.length).toBeGreaterThan(0)
    expect(availableLocales).toContain(DEFAULT_LOCALE)
  })

  it('点号是平键的一部分，不做层级拆分', () => {
    expect(t('app.brand')).toBe('LifeOS')
    expect(t('form.daily.title')).toBe('今日打卡')
  })

  it('占位符用 {name}，与后端 str.format 一致', () => {
    expect(t('app.idle_lock', { minutes: 10 })).toBe('空闲 10 分钟自动锁定')
  })

  it('解析数据层的 i18n: 引用', () => {
    expect(resolveI18nRef('i18n:metric.daily.mood.mean')).toBe('平均心情')
    // 还没键化的纯文本原样返回
    expect(resolveI18nRef('餐饮')).toBe('餐饮')
  })

  it('查不到的键退回 key 本身，不静默兜底', () => {
    expect(t('no.such.key', { defaultValue: 'no.such.key' })).toBe('no.such.key')
  })

  it('没有词表的语言切不过去', () => {
    setLocale('en-US')
    expect(getLocale()).toBe(DEFAULT_LOCALE)
  })
})
