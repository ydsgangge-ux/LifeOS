/** 朗读的纯函数（P5 W5b）。
 *
 * 朗读全部走浏览器 / 系统内置的语音合成（`speechSynthesis`），**不联网**：
 * 文本只在当前这台机器上合成，不上传、不发请求，和「儿童档不出站」的承诺一致。
 *
 * 这里只放能在 Vitest 里直接测的纯逻辑；带界面的部分在 components/ReadAloud.tsx。
 * 语音输入（录音上传识别）按方案 3.2 决定**不做**，见 ADR-18。
 */

/** 语速范围：太慢或太快对小孩与特殊人群都不友好，钳在这之间。 */
export const MIN_RATE = 0.6
export const MAX_RATE = 1.6
export const DEFAULT_RATE = 1

/** 语速钳制：非有限数（NaN / Infinity）回落到默认值。 */
export function clampRate(rate: number): number {
  if (!Number.isFinite(rate)) return DEFAULT_RATE
  if (rate < MIN_RATE) return MIN_RATE
  if (rate > MAX_RATE) return MAX_RATE
  return rate
}

/** 本机有没有朗读能力。
 *
 * 没有 `speechSynthesis`（或没有可用语音）时返回 false —— 界面据此**不渲染**朗读按钮，
 * 而不是给一个点不动的灰按钮（方案 9「风险」栏的处理办法）。
 */
export function canSpeak(): boolean {
  if (typeof window === 'undefined') return false
  const synth = window.speechSynthesis
  if (!synth || typeof synth.speak !== 'function') return false
  return typeof window.SpeechSynthesisUtterance === 'function'
}

const SENTENCE_END = /[。！？!?；;…\n]/

/** 中文断句：按句末标点（。！？；…）与换行切，标点留在句子末尾。
 *
 * 空串 / 全空白返回空数组；末尾没有标点的那半句也算一句。
 */
export function splitSentences(text: string): string[] {
  const trimmed = text.trim()
  if (!trimmed) return []
  const parts: string[] = []
  let buffer = ''
  for (const char of trimmed) {
    buffer += char
    if (SENTENCE_END.test(char)) {
      const piece = buffer.trim()
      if (piece) parts.push(piece)
      buffer = ''
    }
  }
  const tail = buffer.trim()
  if (tail) parts.push(tail)
  return parts
}
