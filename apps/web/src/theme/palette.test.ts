/** 主题引擎验收测试（第 11.3 节、P2 验收「5 张风格差异大的图片」）。
 *
 * 用 5 组合成像素代表风格差异很大的图片（暖色落日、冷色海洋、森林、纯灰、
 * 高饱和霓虹），验证每张图都能算出亮暗两套且都通过对比度检查。
 */

import { describe, expect, it } from 'vitest'

import { hexToRgb } from './color'
import {
  buildChartColors,
  buildScale,
  buildThemeFromPixels,
  neutralTheme,
  samplePixels,
  tokensPassContrast,
  type ThemeTokens,
} from './palette'

/** 合成 RGBA 像素：每个颜色铺 perColor 个像素并加确定性抖动，模拟真实照片的噪声。 */
function makePixels(colors: string[], perColor = 600): Uint8ClampedArray {
  let seed = 987654321
  const rand = (): number => {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff
    return seed / 0x7fffffff
  }
  const arr = new Uint8ClampedArray(colors.length * perColor * 4)
  let i = 0
  for (const hex of colors) {
    const { r, g, b } = hexToRgb(hex)
    for (let n = 0; n < perColor; n += 1) {
      const jitter = (): number => (rand() - 0.5) * 0.06
      arr[i] = (r + jitter()) * 255
      arr[i + 1] = (g + jitter()) * 255
      arr[i + 2] = (b + jitter()) * 255
      arr[i + 3] = 255
      i += 4
    }
  }
  return arr
}

const STYLES: Record<string, string[]> = {
  暖色落日: ['#ff6b35', '#f7c59f', '#c1121f', '#780000'],
  冷色海洋: ['#03045e', '#0077b6', '#00b4d8', '#caf0f8'],
  森林苔藓: ['#1b4332', '#2d6a4f', '#95d5b2', '#d8f3dc'],
  纯灰极简: ['#111111', '#555555', '#aaaaaa', '#eeeeee'],
  高饱和霓虹: ['#ff00ff', '#00ffff', '#120024', '#ffee00'],
}

function schemed(tokens: ThemeTokens): [string, Record<string, string>][] {
  return [
    ['light', tokens.light],
    ['dark', tokens.dark],
  ]
}

describe('主题引擎：5 张风格图片', () => {
  for (const [name, colors] of Object.entries(STYLES)) {
    it(`${name}：亮暗两套都通过对比度检查`, () => {
      const tokens = buildThemeFromPixels(makePixels(colors))
      for (const [scheme, palette] of schemed(tokens)) {
        expect(tokensPassContrast(palette), `${name}/${scheme}`).toBe(true)
      }
      // 亮暗必须真的不同，而不是同一套
      expect(tokens.light['--bg']).not.toBe(tokens.dark['--bg'])
    })
  }

  it('每套都带 6 个互不相同的图表色', () => {
    const tokens = buildThemeFromPixels(makePixels(STYLES['高饱和霓虹']))
    for (const [, palette] of schemed(tokens)) {
      const charts = [1, 2, 3, 4, 5, 6].map((n) => palette[`--chart-${n}`])
      expect(charts.every(Boolean)).toBe(true)
      expect(new Set(charts).size).toBe(6)
    }
  })

  it('同一张图两次生成完全一致（确定性）', () => {
    const pixels = makePixels(STYLES['暖色落日'])
    expect(buildThemeFromPixels(pixels)).toEqual(buildThemeFromPixels(pixels))
  })

  it('采样会剔除透明与过暗/过亮像素', () => {
    const pixels = new Uint8ClampedArray([
      0, 0, 0, 255, // 过暗
      255, 255, 255, 255, // 过亮
      120, 150, 200, 255, // 有效
      0, 0, 0, 0, // 透明
    ])
    expect(samplePixels(pixels)).toHaveLength(1)
  })
})

describe('兜底与单元', () => {
  it('几乎全黑/全白的图片回退到中性主题且仍过对比度', () => {
    const tokens = buildThemeFromPixels(makePixels(['#000000', '#ffffff']))
    expect(tokensPassContrast(tokens.light)).toBe(true)
    expect(tokensPassContrast(tokens.dark)).toBe(true)
  })

  it('中性兜底主题过对比度', () => {
    const tokens = neutralTheme()
    expect(tokensPassContrast(tokens.light)).toBe(true)
    expect(tokensPassContrast(tokens.dark)).toBe(true)
  })

  it('11 阶色阶数量正确且均落在色域内', () => {
    const scale = buildScale({ l: 0.6, c: 0.2, h: 140 })
    expect(scale).toHaveLength(11)
    expect(scale.every((hex) => /^#[0-9a-f]{6}$/.test(hex))).toBe(true)
  })

  it('图表色始终返回 6 个', () => {
    expect(buildChartColors('#0071e3')).toHaveLength(6)
    expect(buildChartColors('#ff00ff')).toHaveLength(6)
  })
})
