/** 图片 → 主题的浏览器侧胶水（第 11.3 节第 1 步）。
 *
 * 读取与缩放全在本地：File API + createImageBitmap，缩到最长边 128px 的画布后
 * 取像素喂给纯函数管线。图片不上传、不出站；只有用户点了「保存」才把 token
 * 与原图（作为加密附件）写进档案。
 */

import { buildThemeFromPixels, type ThemeTokens } from './palette'

/** 缩放目标：最长边 128px 足够取色，也够快（第 11.3 节第 1 步）。 */
const MAX_SIDE = 128

export async function pixelsFromFile(file: File): Promise<Uint8ClampedArray> {
  const bitmap = await createImageBitmap(file)
  try {
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height))
    const width = Math.max(1, Math.round(bitmap.width * scale))
    const height = Math.max(1, Math.round(bitmap.height * scale))
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d', { willReadFrequently: true })
    if (!ctx) throw new Error('浏览器不支持 canvas 2d')
    ctx.drawImage(bitmap, 0, 0, width, height)
    return ctx.getImageData(0, 0, width, height).data
  } finally {
    bitmap.close()
  }
}

/** 从图片文件算出一套亮暗主题（不上传图片）。 */
export async function themeFromFile(file: File): Promise<ThemeTokens> {
  return buildThemeFromPixels(await pixelsFromFile(file))
}
