/** 取色与 token 生成（第 11.3 节主题引擎第 4–10 步）。
 *
 * 输入是图像像素（RGBA），输出是亮暗两套语义 token。全部纯函数，可在 Vitest 里
 * 用合成像素直接验证「任意风格图片都能得到通过对比度检查的亮暗主题」。
 */

import {
  clampChroma,
  contrastRatio,
  hexToOklab,
  hexToOklch,
  oklabDistance,
  oklabToOklch,
  oklchToHex,
  readableOn,
  relativeLuminance,
  rgbToOklab,
  simulateDeuteranopia,
  type Oklab,
  type Oklch,
} from './color'

/** 明度色阶固定 11 档（对应 50…950），色度按色域裁剪（第 11.3 节第 5 步）。 */
const SCALE_L = [0.98, 0.95, 0.9, 0.83, 0.74, 0.64, 0.54, 0.45, 0.36, 0.27, 0.18] as const
const IDX = {
  n50: 0,
  n100: 1,
  n200: 2,
  n300: 3,
  n400: 4,
  n500: 5,
  n600: 6,
  n700: 7,
  n800: 8,
  n900: 9,
  n950: 10,
} as const

/** 语义状态色固定色相，只随主题调明度，避免「成功/警告/危险」被图片带偏（第 9 步）。 */
const STATUS_HUES = { success: 149, warning: 70, danger: 27 } as const

/** 图表色不满足可辨性时回退的预置色盲友好色板（Okabe–Ito 取 6 色）。 */
const COLORBLIND_SAFE = [
  '#0072b2',
  '#e69f00',
  '#009e73',
  '#d55e00',
  '#56b4e9',
  '#cc79a7',
]

/** 中性主题（没有合格主色簇，或图片不可读时的兜底）：低饱和强调色。 */
const DEFAULT_ROLES: Roles = {
  primary: { l: 0.54, c: 0.13, h: 250 },
  accent: { l: 0.6, c: 0.1, h: 300 },
  neutral: { l: 0.5, c: 0.015, h: 250 },
  fromImage: false,
}

export interface ThemeTokens {
  light: Record<string, string>
  dark: Record<string, string>
}

interface Roles {
  primary: Oklch
  accent: Oklch
  neutral: Oklch
  /** 主色是否真的来自图片（否则是低饱和兜底，前端可据此提示）。 */
  fromImage: boolean
}

interface Cluster {
  centroid: Oklab
  size: number
  share: number
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

/** 确定性伪随机：测试要可复现，同一张图必须给出同一套主题。 */
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 采样：去掉透明像素与过暗/过亮像素（第 11.3 节第 2 步）。 */
export function samplePixels(pixels: ArrayLike<number>): Oklab[] {
  const out: Oklab[] = []
  for (let i = 0; i + 3 < pixels.length; i += 4) {
    if (pixels[i + 3] / 255 < 0.5) continue
    const lab = rgbToOklab({
      r: pixels[i] / 255,
      g: pixels[i + 1] / 255,
      b: pixels[i + 2] / 255,
    })
    if (lab.L < 0.08 || lab.L > 0.97) continue
    out.push(lab)
  }
  return out
}

/** k-means++ 初始化 + Lloyd 迭代（第 11.3 节第 3 步，k=6、最多 20 次）。 */
export function kmeans(
  samples: Oklab[],
  k = 6,
  seed = 20261016,
  maxIter = 20,
): Cluster[] {
  if (samples.length === 0) return []
  const rand = mulberry32(seed)
  const effectiveK = Math.min(k, samples.length)

  const centroids: Oklab[] = [samples[Math.floor(rand() * samples.length)]]
  while (centroids.length < effectiveK) {
    const d2 = samples.map((p) => minDist2(p, centroids))
    const total = d2.reduce((acc, v) => acc + v, 0)
    let pick: Oklab
    if (total <= 0) {
      pick = samples[centroids.length % samples.length]
    } else {
      let target = rand() * total
      let idx = 0
      for (; idx < d2.length; idx += 1) {
        target -= d2[idx]
        if (target <= 0) break
      }
      pick = samples[Math.min(idx, d2.length - 1)]
    }
    centroids.push(pick)
  }

  const assignment = new Array<number>(samples.length).fill(0)
  for (let iter = 0; iter < maxIter; iter += 1) {
    let moved = 0
    const sums = centroids.map(() => ({ L: 0, a: 0, b: 0, n: 0 }))
    for (let i = 0; i < samples.length; i += 1) {
      const idx = nearest(samples[i], centroids)
      if (idx !== assignment[i]) moved += 1
      assignment[i] = idx
      const bucket = sums[idx]
      bucket.L += samples[i].L
      bucket.a += samples[i].a
      bucket.b += samples[i].b
      bucket.n += 1
    }
    for (let c = 0; c < centroids.length; c += 1) {
      const bucket = sums[c]
      if (bucket.n > 0) {
        centroids[c] = { L: bucket.L / bucket.n, a: bucket.a / bucket.n, b: bucket.b / bucket.n }
      }
    }
    if (moved === 0) break
  }

  const counts = new Array<number>(centroids.length).fill(0)
  for (const idx of assignment) counts[idx] += 1
  return centroids
    .map((centroid, i) => ({
      centroid,
      size: counts[i],
      share: counts[i] / samples.length,
    }))
    .filter((c) => c.size > 0)
}

function minDist2(p: Oklab, centroids: Oklab[]): number {
  let best = Infinity
  for (const c of centroids) {
    const d = (p.L - c.L) ** 2 + (p.a - c.a) ** 2 + (p.b - c.b) ** 2
    if (d < best) best = d
  }
  return best
}

function nearest(p: Oklab, centroids: Oklab[]): number {
  let best = 0
  let bestD = Infinity
  for (let i = 0; i < centroids.length; i += 1) {
    const c = centroids[i]
    const d = (p.L - c.L) ** 2 + (p.a - c.a) ** 2 + (p.b - c.b) ** 2
    if (d < bestD) {
      bestD = d
      best = i
    }
  }
  return best
}

/** 选色（第 11.3 节第 4 步）：按「簇大小 × 色度」打分。 */
export function pickRoles(clusters: Cluster[], samples: Oklab[]): Roles {
  const scored = clusters
    .map((c) => {
      const lch = oklabToOklch(c.centroid)
      return { lch, chroma: lch.c, share: c.share, score: c.size * lch.c }
    })
    .sort((x, y) => y.score - x.score)

  const primaryCandidates = scored.filter((c) => c.chroma >= 0.06 && c.share >= 0.05)
  const primary = primaryCandidates[0]

  if (!primary) return { ...DEFAULT_ROLES }

  let accent = scored.find(
    (c) =>
      c !== primary &&
      c.chroma >= 0.06 &&
      hueGap(c.lch.h, primary.lch.h) >= 60,
  )
  if (!accent) {
    accent = {
      lch: { l: 0.62, c: 0.08, h: (primary.lch.h + 180) % 360 },
      chroma: 0.08,
      share: 0,
      score: 0,
    }
  }

  // 中性：图片平均色相，色度压到 0.01–0.02，避免整体发灰发脏（第 4 步）。
  const mean = meanHue(samples)
  const neutral: Oklch = { l: 0.5, c: clamp(mean.c, 0.01, 0.02), h: mean.h }

  return {
    primary: { l: primary.lch.l, c: primary.lch.c, h: primary.lch.h },
    accent: { l: accent.lch.l, c: clamp(accent.lch.c, 0.04, 0.16), h: accent.lch.h },
    neutral,
    fromImage: true,
  }
}

function hueGap(a: number, b: number): number {
  const diff = Math.abs(a - b) % 360
  return diff > 180 ? 360 - diff : diff
}

function meanHue(samples: Oklab[]): { c: number; h: number } {
  let sa = 0
  let sb = 0
  for (const p of samples) {
    sa += p.a
    sb += p.b
  }
  const a = sa / samples.length
  const b = sb / samples.length
  const c = Math.sqrt(a * a + b * b)
  let h = (Math.atan2(b, a) * 180) / Math.PI
  if (h < 0) h += 360
  return { c, h }
}

/** 生成 11 阶色（第 5 步）：明度固定，色度按色域裁剪。 */
export function buildScale(base: Oklch): string[] {
  return SCALE_L.map((l) => oklchToHex(clampChroma({ l, c: base.c, h: base.h })))
}

function mixHex(a: string, b: string, t: number): string {
  const la = hexToOklab(a)
  const lb = hexToOklab(b)
  return oklchToHex(
    oklabToOklch({
      L: la.L + (lb.L - la.L) * t,
      a: la.a + (lb.a - la.a) * t,
      b: la.b + (lb.b - la.b) * t,
    }),
  )
}

/** 对比度校验与回退（第 7 步）：沿明度最多调 12 步，仍不达标则回退中性文字色。 */
export function fixContrast(hex: string, background: string, minRatio: number): string {
  if (contrastRatio(hex, background) >= minRatio) return hex
  const darken = relativeLuminance(background) > 0.5
  const lch = hexToOklch(hex)
  for (let step = 1; step <= 12; step += 1) {
    const l = clamp(darken ? lch.l - 0.02 * step : lch.l + 0.02 * step, 0.03, 0.99)
    const candidate = oklchToHex(clampChroma({ l, c: lch.c, h: lch.h }))
    if (contrastRatio(candidate, background) >= minRatio) return candidate
  }
  return darken ? '#000000' : '#ffffff'
}

/** 图表色（第 8 步）：以 primary 色相为首、OKLCH 等间隔 6 色，不可辨则回退预置板。 */
export function buildChartColors(primaryHex: string): string[] {
  const baseHue = hexToOklch(primaryHex).h
  const generated = [0, 60, 120, 180, 240, 300].map((offset) =>
    oklchToHex({ l: 0.65, c: 0.12, h: (baseHue + offset) % 360 }),
  )
  const labs = generated.map(hexToOklab)
  const simulated = generated.map(simulateDeuteranopia)

  let adjacentOk = true
  for (let i = 1; i < labs.length; i += 1) {
    if (oklabDistance(labs[i - 1], labs[i]) < 0.08) adjacentOk = false
  }
  let cvdOk = true
  for (let i = 0; i < simulated.length; i += 1) {
    for (let j = i + 1; j < simulated.length; j += 1) {
      if (oklabDistance(simulated[i], simulated[j]) < 0.05) cvdOk = false
    }
  }
  return adjacentOk && cvdOk ? generated : [...COLORBLIND_SAFE]
}

/** 语义状态色（第 9 步）：色相固定，只按主题调明度并做对比度回退。 */
function buildStatus(scheme: 'light' | 'dark', background: string): Record<string, string> {
  const baseL = scheme === 'light' ? 0.6 : 0.72
  const out: Record<string, string> = {}
  for (const [name, hue] of Object.entries(STATUS_HUES)) {
    const base = oklchToHex({ l: baseL, c: 0.16, h: hue })
    out[name] = fixContrast(base, background, 3)
  }
  return out
}

/** 一整套语义 token（第 6–10 步）。背景用浅灰而非纯白，贴合白-灰 Mac 风偏好。 */
export function buildSemantic(roles: Roles, scheme: 'light' | 'dark'): Record<string, string> {
  const n = buildScale(roles.neutral)
  const p = buildScale(roles.primary)
  const a = buildScale(roles.accent)
  const light = scheme === 'light'

  const bg = light ? n[IDX.n100] : n[IDX.n950]
  const surface = light ? n[IDX.n50] : n[IDX.n900]
  const surface2 = mixHex(surface, bg, 0.5)
  const text = light ? n[IDX.n900] : n[IDX.n50]
  const mutedBase = light ? n[IDX.n600] : n[IDX.n400]
  const border = light ? n[IDX.n200] : n[IDX.n800]
  const borderStrong = light ? n[IDX.n300] : n[IDX.n700]
  const primaryBase = light ? p[IDX.n600] : p[IDX.n400]
  const accentBase = light ? a[IDX.n500] : a[IDX.n400]

  const primary = fixContrast(primaryBase, bg, 3)
  const accent = fixContrast(accentBase, bg, 3)
  const status = buildStatus(scheme, bg)
  const charts = buildChartColors(primary)

  const tokens: Record<string, string> = {
    '--bg': bg,
    '--surface': surface,
    '--surface-2': surface2,
    '--text': fixContrast(text, bg, 4.5),
    '--text-muted': fixContrast(mutedBase, bg, 4.5),
    '--border': border,
    '--border-strong': borderStrong,
    '--primary': primary,
    '--on-primary': readableOn(primary),
    '--accent': accent,
    '--success': status.success,
    '--warning': status.warning,
    '--danger': status.danger,
  }
  charts.forEach((color, i) => {
    tokens[`--chart-${i + 1}`] = color
  })
  return tokens
}

/** 图片采样太少、无法取色时的兜底主题（仍是完整可用的亮暗两套）。 */
export function neutralTheme(): ThemeTokens {
  return {
    light: buildSemantic(DEFAULT_ROLES, 'light'),
    dark: buildSemantic(DEFAULT_ROLES, 'dark'),
  }
}

/** 从像素算出一套亮暗主题（第 11.3 节完整管线：采样 → 聚类 → 选色 → 出 token）。 */
export function buildThemeFromPixels(pixels: ArrayLike<number>): ThemeTokens {
  const samples = samplePixels(pixels)
  if (samples.length < 8) return neutralTheme()
  const clusters = kmeans(samples)
  const roles = pickRoles(clusters, samples)
  return {
    light: buildSemantic(roles, 'light'),
    dark: buildSemantic(roles, 'dark'),
  }
}

/** token 值是否满足无障碍对比度（验收用：亮暗两套都过）。 */
export function tokensPassContrast(tokens: Record<string, string>): boolean {
  const bg = tokens['--bg']
  const surface = tokens['--surface']
  const checks: [string, string, number][] = [
    [tokens['--text'], bg, 4.5],
    [tokens['--text'], surface, 4.5],
    [tokens['--text-muted'], bg, 4.5],
    [tokens['--text-muted'], surface, 4.5],
    [tokens['--primary'], bg, 3],
    [tokens['--accent'], bg, 3],
    [tokens['--success'], bg, 3],
    [tokens['--warning'], bg, 3],
    [tokens['--danger'], bg, 3],
  ]
  return checks.every(([fg, against, min]) => contrastRatio(fg, against) >= min)
}
