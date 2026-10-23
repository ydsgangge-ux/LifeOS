/** 手机访问（局域网）：临时开一个端口，用二维码把带令牌的地址交给手机。
 *
 * 默认关闭，点「开启」才监听 0.0.0.0；锁定、空闲超时或 30 分钟到期后
 * 服务端会自己收回端口，这里只是把状态展示出来。
 *
 * 必须把代价写明白：这一档走 HTTP 明文，同一 Wi-Fi 下抓包就能看到数据与令牌，
 * 所以只在可信网络里短时间用。
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { QRCodeSVG } from 'qrcode.react'

import { api } from '../lib/api'
import type { LanStatus } from '../lib/types'

interface Props {
  onNotice: (kind: 'ok' | 'error', text: string) => void
}

export function LanAccess({ onNotice }: Props) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<LanStatus>({ active: false })
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)

  const toggle = async () => {
    if (open) {
      setOpen(false)
      return
    }
    setOpen(true)
    try {
      setStatus(await api.getLan())
    } catch {
      // 状态读不到不影响开启，保持关闭态即可
    }
  }

  const start = async () => {
    setBusy(true)
    setCopied(false)
    try {
      setStatus(await api.startLan())
    } catch {
      onNotice('error', t('lan.failed'))
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    setBusy(true)
    try {
      setStatus(await api.stopLan())
    } catch {
      onNotice('error', t('lan.failed'))
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    if (!status.url) return
    try {
      await navigator.clipboard.writeText(status.url)
      setCopied(true)
    } catch {
      // 浏览器不给剪贴板权限时，用户还能手动选中上面的地址
    }
  }

  const minutes = Math.max(1, Math.ceil((status.expires_in_seconds ?? 0) / 60))

  return (
    <div className="lan">
      <button className="ghost" onClick={() => void toggle()}>
        {t('lan.button')}
      </button>
      {open && (
        <div className="lan-panel card">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <h2>{t('lan.title')}</h2>
            <button className="ghost" onClick={() => setOpen(false)}>
              {t('lan.close')}
            </button>
          </div>
          <p className="muted">{t('lan.intro')}</p>
          <div className="notice warn" style={{ marginBottom: 'var(--space-3)' }}>
            {t('lan.warn')}
          </div>

          {status.active && status.url ? (
            <>
              <div className="lan-qr">
                <QRCodeSVG value={status.url} size={168} level="M" marginSize={2} />
              </div>
              <p className="muted" style={{ textAlign: 'center' }}>
                {t('lan.scan_hint')}
              </p>
              <div className="recovery-key">{status.url}</div>
              <div className="row wrap">
                <button onClick={() => void copy()}>
                  {copied ? t('lan.copied') : t('lan.copy')}
                </button>
                <button className="danger" disabled={busy} onClick={() => void stop()}>
                  {t('lan.stop')}
                </button>
              </div>
              <p className="muted">{t('lan.expires', { minutes })}</p>
            </>
          ) : (
            <button className="primary" disabled={busy} onClick={() => void start()}>
              {busy ? t('lan.starting') : t('lan.start')}
            </button>
          )}

          <p className="muted" style={{ marginBottom: 0 }}>
            {t('lan.firewall')}
          </p>
        </div>
      )}
    </div>
  )
}
