import { beforeEach, describe, expect, it, vi } from 'vitest'

const { setProfileLocale } = vi.hoisted(() => ({ setProfileLocale: vi.fn() }))
vi.mock('./api', () => ({ api: { setProfileLocale } }))

import { DEFAULT_LOCALE, getLocale, setLocale } from '../i18n'
import { changeLocale, localeLabel, localeOptions } from './locale'

/** 等 i18next 的语言变更落地（changeLanguage 返回 Promise，变更在微任务里生效）。 */
const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('语言切换策略', () => {
  beforeEach(async () => {
    setProfileLocale.mockReset()
    setLocale(DEFAULT_LOCALE)
    await flush()
  })

  it('选项用各语言自己的写法，不翻译语言名', () => {
    const codes = localeOptions().map((option) => option.code)
    expect(codes).toContain(DEFAULT_LOCALE)
    expect(codes).toContain('en')
    // 英文界面里也要能找到「简体中文」和「日本語」
    expect(localeLabel(DEFAULT_LOCALE)).toBe('简体中文')
    expect(localeLabel('ja')).toBe('日本語')
    expect(localeLabel('es')).toBe('Español')
    // 没登记的语言码原样显示，不做猜测
    expect(localeLabel('xx-XX')).toBe('xx-XX')
  })

  it('未解锁时只切界面，不调后端', async () => {
    const result = await changeLocale('en', null)
    expect(result).toEqual({ locale: 'en', sync: 'skipped' })
    expect(setProfileLocale).not.toHaveBeenCalled()
    await flush()
    expect(getLocale()).toBe('en')
  })

  it('已解锁时把选择同步进档案', async () => {
    setProfileLocale.mockResolvedValue({ locale: 'en' })
    const result = await changeLocale('en', 'profile-1')
    expect(result).toEqual({ locale: 'en', sync: 'ok' })
    expect(setProfileLocale).toHaveBeenCalledWith('profile-1', 'en')
    await flush()
    expect(getLocale()).toBe('en')
  })

  it('同步失败不阻断：界面已切好，只报告没落档案', async () => {
    setProfileLocale.mockRejectedValue(new Error('boom'))
    const result = await changeLocale('en', 'profile-1')
    expect(result.sync).toBe('failed')
    await flush()
    expect(getLocale()).toBe('en')
  })

  it('没有词表的语言切不过去，也不去同步', async () => {
    const result = await changeLocale('xx-XX', 'profile-1')
    expect(result.sync).toBe('skipped')
    expect(setProfileLocale).not.toHaveBeenCalled()
    await flush()
    expect(getLocale()).toBe(DEFAULT_LOCALE)
  })
})
