/** 朗读纯函数单测（P5 W5b、方案 5b.5）。
 *
 * 测试环境是 node（vite.config.ts 的 `test.environment: 'node'`），没有 window，
 * 正好用来验证「无 speechSynthesis 时 canSpeak() 为 false」。
 */

import { describe, expect, it } from 'vitest'

import { DEFAULT_RATE, MAX_RATE, MIN_RATE, canSpeak, clampRate, splitSentences } from './readAloud'

describe('canSpeak', () => {
  it('没有 window / speechSynthesis 时为 false', () => {
    expect(typeof window).toBe('undefined')
    expect(canSpeak()).toBe(false)
  })
})

describe('clampRate', () => {
  it('把语速钳在 [MIN, MAX] 之间', () => {
    expect(clampRate(0.1)).toBe(MIN_RATE)
    expect(clampRate(9)).toBe(MAX_RATE)
    expect(clampRate(1.25)).toBe(1.25)
  })

  it('非有限数回落到默认语速', () => {
    expect(clampRate(Number.NaN)).toBe(DEFAULT_RATE)
    expect(clampRate(Number.POSITIVE_INFINITY)).toBe(DEFAULT_RATE)
  })
})

describe('splitSentences', () => {
  it('按中文句末标点断句，标点留在句末', () => {
    expect(splitSentences('今天很开心。明天也要加油！')).toEqual(['今天很开心。', '明天也要加油！'])
  })

  it('空串与全空白返回空数组', () => {
    expect(splitSentences('')).toEqual([])
    expect(splitSentences('   \n  ')).toEqual([])
  })

  it('末尾没有标点的半句也算一句', () => {
    expect(splitSentences('先说一句。没写完的半句')).toEqual(['先说一句。', '没写完的半句'])
  })

  it('换行与英文标点同样能断句', () => {
    expect(splitSentences('第一行\n第二行;第三句?')).toEqual(['第一行', '第二行;', '第三句?'])
  })
})
