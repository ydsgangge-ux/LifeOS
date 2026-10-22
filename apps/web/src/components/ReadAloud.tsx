/** 朗读控件（P5 W5b、方案 5b.2）。
 *
 * 走浏览器 / 系统内置语音合成，不联网（方案 3.2）。播放 / 暂停 / 继续 / 停止 + 语速；
 * 逐句高亮；卸载与切换文本时立即 `cancel()`，避免关掉界面后还在后台念。
 *
 * 本机没有朗读能力时**不渲染**（canSpeak() 为 false），而不是留一个点不动的灰按钮。
 * 语音输入（录音上传到云端识别）按方案 3.2 决定不做，见 ADR-18。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { DEFAULT_RATE, canSpeak, clampRate, splitSentences } from '../lib/readAloud'

const RATE_KEY = 'lifeos.readAloud.rate'
const RATE_OPTIONS = [0.75, 1, 1.25, 1.5] as const
const RATE_LABEL_KEY: Record<number, string> = {
  0.75: 'readAloud.rate.slow',
  1: 'readAloud.rate.normal',
  1.25: 'readAloud.rate.fast',
  1.5: 'readAloud.rate.faster',
}

/** 语速选择记在 localStorage（方案 5b.4）：只是本机偏好，不进服务端。 */
function readStoredRate(): number {
  try {
    const raw =
      typeof localStorage === 'undefined' ? null : localStorage.getItem(RATE_KEY)
    return raw ? clampRate(Number(raw)) : DEFAULT_RATE
  } catch {
    return DEFAULT_RATE
  }
}

interface Props {
  text: string
  /** true：连文字一起渲染（逐句高亮）；false：只渲染一排按钮。 */
  renderText?: boolean
  lang?: string
}

export function ReadAloud({ text, renderText = false, lang = 'zh-CN' }: Props) {
  const { t } = useTranslation()
  const sentences = useMemo(() => splitSentences(text), [text])
  const supported = useMemo(() => canSpeak(), [])

  const [status, setStatus] = useState<'idle' | 'speaking' | 'paused'>('idle')
  const [index, setIndex] = useState(-1)
  const [rate, setRate] = useState(readStoredRate)

  // 每次播放拿一个自增 token；stop / 换文本 / 卸载时 token 加一，
  // 过期 utterance 的 onend 回调据此不再继续，避免 cancel 被当成播完而失控。
  const tokenRef = useRef(0)
  const rateRef = useRef(rate)

  const cancelAll = useCallback(() => {
    tokenRef.current += 1
    if (typeof window !== 'undefined') window.speechSynthesis?.cancel()
    setStatus('idle')
    setIndex(-1)
  }, [])

  const playFrom = useCallback(
    (start: number, token: number) => {
      if (token !== tokenRef.current) return
      if (start >= sentences.length) {
        setStatus('idle')
        setIndex(-1)
        return
      }
      const synth = window.speechSynthesis
      const utterance = new window.SpeechSynthesisUtterance(sentences[start])
      utterance.lang = lang
      utterance.rate = rateRef.current
      utterance.onend = () => {
        if (token !== tokenRef.current) return
        playFrom(start + 1, token)
      }
      setIndex(start)
      setStatus('speaking')
      synth.speak(utterance)
    },
    [lang, sentences],
  )

  const start = useCallback(() => {
    if (!supported || sentences.length === 0) return
    window.speechSynthesis.cancel()
    tokenRef.current += 1
    playFrom(0, tokenRef.current)
  }, [playFrom, sentences.length, supported])

  const pause = useCallback(() => {
    window.speechSynthesis.pause()
    setStatus('paused')
  }, [])

  const resume = useCallback(() => {
    window.speechSynthesis.resume()
    setStatus('speaking')
  }, [])

  // 换文本 / 卸载都要立刻停：不然界面都切走了，声音还在后台继续。
  useEffect(() => cancelAll, [text, cancelAll])

  const changeRate = (value: number) => {
    const next = clampRate(value)
    rateRef.current = next
    setRate(next)
    try {
      localStorage.setItem(RATE_KEY, String(next))
    } catch {
      // 存不进去（隐私模式等）不影响本次朗读
    }
    if (status !== 'idle') {
      window.speechSynthesis.cancel()
      tokenRef.current += 1
      playFrom(Math.max(index, 0), tokenRef.current)
    }
  }

  if (!supported || sentences.length === 0) return null

  return (
    <div className={`read-aloud${renderText ? ' with-text' : ''}`}>
      <div className="read-aloud-controls">
        {status === 'idle' ? (
          <button type="button" className="ra-btn" onClick={start}>
            {t('readAloud.play')}
          </button>
        ) : (
          <>
            {status === 'paused' ? (
              <button type="button" className="ra-btn" onClick={resume}>
                {t('readAloud.resume')}
              </button>
            ) : (
              <button type="button" className="ra-btn" onClick={pause}>
                {t('readAloud.pause')}
              </button>
            )}
            <button type="button" className="ra-btn ghost" onClick={cancelAll}>
              {t('readAloud.stop')}
            </button>
          </>
        )}
        <label className="ra-rate">
          {t('readAloud.rate')}
          <select value={rate} onChange={(event) => changeRate(Number(event.target.value))}>
            {RATE_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {t(RATE_LABEL_KEY[option])}
              </option>
            ))}
          </select>
        </label>
      </div>

      {renderText && (
        <p className="read-aloud-text">
          {sentences.map((sentence, position) => (
            <span
              key={position}
              className={
                position === index && status !== 'idle'
                  ? 'ra-sentence active'
                  : 'ra-sentence'
              }
            >
              {sentence}
            </span>
          ))}
        </p>
      )}
    </div>
  )
}
