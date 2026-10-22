/** 色彩基础运算测试（第 11.3 节主题引擎底座）。 */

import { describe, expect, it } from 'vitest'

import {
  clampChroma,
  contrastRatio,
  hexToOklab,
  hexToOklch,
  inSrgbGamut,
  oklabDistance,
  oklabToOklch,
  oklchToHex,
  oklchToOklab,
  readableOn,
  rgbToHex,
  rgbToOklab,
  hexToRgb,
} from './color'

describe('颜色空间转换', () => {
  it('hex 与 rgb 往返一致', () => {
    expect(rgbToHex(hexToRgb('#0071e3'))).toBe('#0071e3')
    expect(rgbToHex(hexToRgb('#fff'))).toBe('#ffffff')
  })

  it('OKLab 往返误差极小', () => {
    for (const hex of ['#0071e3', '#ff6b35', '#2d6a4f', '#111111', '#eeeeee']) {
      const lab = hexToOklab(hex)
      const back = oklchToHex(oklabToOklch(lab))
      const rgb = hexToRgb(hex)
      const backRgb = hexToRgb(back)
      expect(Math.abs(rgb.r - backRgb.r)).toBeLessThan(0.01)
      expect(Math.abs(rgb.g - backRgb.g)).toBeLessThan(0.01)
      expect(Math.abs(rgb.b - backRgb.b)).toBeLessThan(0.01)
    }
  })

  it('灰阶色度接近 0', () => {
    expect(hexToOklch('#808080').c).toBeLessThan(0.005)
  })
})

describe('WCAG 对比度', () => {
  it('白黑对比度为 21', () => {
    expect(contrastRatio('#ffffff', '#000000')).toBeCloseTo(21, 5)
  })

  it('同色对比度为 1', () => {
    expect(contrastRatio('#777777', '#777777')).toBeCloseTo(1, 5)
  })

  it('浅底选黑字、深底选白字', () => {
    expect(readableOn('#f5f5f7')).toBe('#000000')
    expect(readableOn('#101014')).toBe('#ffffff')
  })
})

describe('色域裁剪', () => {
  it('裁剪后落在 sRGB 内', () => {
    const wild = { l: 0.6, c: 0.5, h: 140 }
    expect(inSrgbGamut(oklchToOklab(wild))).toBe(false)
    const safe = clampChroma(wild)
    expect(inSrgbGamut(oklchToOklab(safe))).toBe(true)
    expect(safe.c).toBeLessThan(wild.c)
  })

  it('已在色域内的颜色不被改动', () => {
    const inside = { l: 0.6, c: 0.05, h: 250 }
    expect(clampChroma(inside).c).toBeCloseTo(0.05, 5)
  })
})

describe('OKLab 距离与 RGB 转换', () => {
  it('感知距离随差异增大', () => {
    const near = oklabDistance(hexToOklab('#808080'), hexToOklab('#8a8a8a'))
    const far = oklabDistance(hexToOklab('#808080'), hexToOklab('#ffffff'))
    expect(near).toBeLessThan(far)
  })

  it('rgbToOklab 明度随亮度单调', () => {
    const dark = rgbToOklab({ r: 0.1, g: 0.1, b: 0.1 })
    const light = rgbToOklab({ r: 0.9, g: 0.9, b: 0.9 })
    expect(dark.L).toBeLessThan(light.L)
  })
})
