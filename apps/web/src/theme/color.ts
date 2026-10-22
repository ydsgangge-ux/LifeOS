/** 色彩基础运算（第 11.3 节主题引擎的底座）。
 *
 * 全部是纯函数，不碰 DOM，便于 Vitest 直接测试。色彩空间：
 *   - sRGB（0–255 或 0–1 的 gamma 编码值）
 *   - OKLab（感知均匀的直角坐标，L 0–1）
 *   - OKLCH（极坐标：明度 L、色度 C、色相 h 度）
 * 对比度按 WCAG 2.2：正文 4.5:1，大字/图标 3:1。
 *
 * 之所以不引第三方色彩库：CSP 与「构建产物不含外部引用」的要求之外，
 * 自己实现能让色域裁剪、对比度回退这些关键步骤完全可测、可解释。
 */

export interface Rgb {
  r: number
  g: number
  b: number
}

/** OKLab：L 明度（0–1），a/b 是直角色度分量。 */
export interface Oklab {
  L: number
  a: number
  b: number
}

/** OKLCH：极坐标写法，h 为色相角度（0–360）。 */
export interface Oklch {
  l: number
  c: number
  h: number
}

const clamp = (value: number, lo: number, hi: number): number =>
  Math.min(hi, Math.max(lo, value))

/** sRGB 编码值 → 线性光（WCAG 亮度与 OKLab 转换都要先走这一步）。 */
export function srgbToLinear(channel: number): number {
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
}

export function linearToSrgb(channel: number): number {
  return channel <= 0.0031308 ? channel * 12.92 : 1.055 * channel ** (1 / 2.4) - 0.055
}

function rgbToLinearRgb(rgb: Rgb): Rgb {
  return { r: srgbToLinear(rgb.r), g: srgbToLinear(rgb.g), b: srgbToLinear(rgb.b) }
}

function linearRgbToRgb(linear: Rgb): Rgb {
  return {
    r: linearToSrgb(linear.r),
    g: linearToSrgb(linear.g),
    b: linearToSrgb(linear.b),
  }
}

/** sRGB（0–1）→ OKLab。系数来自 Björn Ottosson 的推导。 */
export function rgbToOklab(rgb: Rgb): Oklab {
  const { r, g, b } = rgbToLinearRgb(rgb)
  const l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
  const m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
  const s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
  const l_ = Math.cbrt(l)
  const m_ = Math.cbrt(m)
  const s_ = Math.cbrt(s)
  return {
    L: 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_,
    a: 1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_,
    b: 0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_,
  }
}

/** OKLab → sRGB（0–1）。可能略越界，调用方自行裁剪或用色域判断。 */
export function oklabToRgb(lab: Oklab): Rgb {
  const l_ = lab.L + 0.3963377774 * lab.a + 0.2158037573 * lab.b
  const m_ = lab.L - 0.1055613458 * lab.a - 0.0638541728 * lab.b
  const s_ = lab.L - 0.0894841775 * lab.a - 1.291485548 * lab.b
  const l = l_ ** 3
  const m = m_ ** 3
  const s = s_ ** 3
  return linearRgbToRgb({
    r: 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    g: -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    b: -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  })
}

export function oklabToOklch(lab: Oklab): Oklch {
  const c = Math.sqrt(lab.a * lab.a + lab.b * lab.b)
  let h = (Math.atan2(lab.b, lab.a) * 180) / Math.PI
  if (h < 0) h += 360
  return { l: lab.L, c, h }
}

export function oklchToOklab(lch: Oklch): Oklab {
  const rad = (lch.h * Math.PI) / 180
  return { L: lch.l, a: Math.cos(rad) * lch.c, b: Math.sin(rad) * lch.c }
}

/** 是否在 sRGB 色域内（带极小容差，避免浮点抖动被误判越界）。 */
export function inSrgbGamut(lab: Oklab, epsilon = 1e-4): boolean {
  const rgb = oklabToRgb(lab)
  return (
    rgb.r >= -epsilon &&
    rgb.r <= 1 + epsilon &&
    rgb.g >= -epsilon &&
    rgb.g <= 1 + epsilon &&
    rgb.b >= -epsilon &&
    rgb.b <= 1 + epsilon
  )
}

/** 色域裁剪：固定 L 与 h，二分找色度上界（对应 culori 的 clampChroma）。 */
export function clampChroma(lch: Oklch): Oklch {
  const target: Oklch = { l: clamp(lch.l, 0, 1), c: Math.max(0, lch.c), h: lch.h }
  if (inSrgbGamut(oklchToOklab(target))) return target
  let lo = 0
  let hi = target.c
  for (let i = 0; i < 24; i += 1) {
    const mid = (lo + hi) / 2
    if (inSrgbGamut(oklchToOklab({ ...target, c: mid }))) lo = mid
    else hi = mid
  }
  return { ...target, c: lo }
}

/** OKLCH → 十六进制（先裁色域再量化到 8 位）。 */
export function oklchToHex(lch: Oklch): string {
  const safe = clampChroma(lch)
  const rgb = oklabToRgb(oklchToOklab(safe))
  return rgbToHex({
    r: clamp(rgb.r, 0, 1),
    g: clamp(rgb.g, 0, 1),
    b: clamp(rgb.b, 0, 1),
  })
}

export function hexToRgb(hex: string): Rgb {
  const value = hex.trim().replace(/^#/, '')
  const full =
    value.length === 3
      ? value
          .split('')
          .map((c) => c + c)
          .join('')
      : value
  const num = Number.parseInt(full, 16)
  return {
    r: ((num >> 16) & 0xff) / 255,
    g: ((num >> 8) & 0xff) / 255,
    b: (num & 0xff) / 255,
  }
}

export function rgbToHex(rgb: Rgb): string {
  const channel = (value: number): string =>
    Math.round(clamp(value, 0, 1) * 255)
      .toString(16)
      .padStart(2, '0')
  return `#${channel(rgb.r)}${channel(rgb.g)}${channel(rgb.b)}`
}

export function hexToOklch(hex: string): Oklch {
  return oklabToOklch(rgbToOklab(hexToRgb(hex)))
}

export function hexToOklab(hex: string): Oklab {
  return rgbToOklab(hexToRgb(hex))
}

/** WCAG 相对亮度（用线性光分量）。 */
export function relativeLuminance(color: Rgb | string): number {
  const rgb = typeof color === 'string' ? hexToRgb(color) : color
  return 0.2126 * srgbToLinear(rgb.r) + 0.7152 * srgbToLinear(rgb.g) + 0.0722 * srgbToLinear(rgb.b)
}

/** WCAG 对比度：1–21。参数可以是十六进制串或 RGB。 */
export function contrastRatio(a: Rgb | string, b: Rgb | string): number {
  const la = relativeLuminance(a)
  const lb = relativeLuminance(b)
  const hi = Math.max(la, lb)
  const lo = Math.min(la, lb)
  return (hi + 0.05) / (lo + 0.05)
}

/** 两色在 OKLab 上的欧氏距离（图表色可辨性用）。 */
export function oklabDistance(x: Oklab, y: Oklab): number {
  return Math.sqrt((x.L - y.L) ** 2 + (x.a - y.a) ** 2 + (x.b - y.b) ** 2)
}

/** 白或黑：取与底色对比度更高的那个，作为「主色上的文字」。 */
export function readableOn(background: string): string {
  return contrastRatio('#ffffff', background) >= contrastRatio('#000000', background)
    ? '#ffffff'
    : '#000000'
}

/** 红绿色盲（deuteranopia）近似模拟：作用于线性光空间。
 *
 * 用 Machado 等 2009 的 deuteranomaly 强档矩阵；只用于「相邻图表色是否还能分辨」
 * 的保守判断，不用于生成颜色，所以近似足够。
 */
const DEUTERANOPIA_MATRIX: readonly number[] = [
  0.367322, 0.860646, -0.227968,
  0.280085, 0.672501, 0.047413,
  -0.011820, 0.042940, 0.968881,
]

export function simulateDeuteranopia(hex: string): Oklab {
  const { r, g, b } = rgbToLinearRgb(hexToRgb(hex))
  const m = DEUTERANOPIA_MATRIX
  const lin: Rgb = {
    r: clamp(m[0] * r + m[1] * g + m[2] * b, 0, 1),
    g: clamp(m[3] * r + m[4] * g + m[5] * b, 0, 1),
    b: clamp(m[6] * r + m[7] * g + m[8] * b, 0, 1),
  }
  return rgbToOklab(linearRgbToRgb(lin))
}
