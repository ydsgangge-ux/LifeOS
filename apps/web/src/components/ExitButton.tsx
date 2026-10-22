/** 退出 LifeOS（P6 W3.2）：界面内唯一能真正终止核心进程的入口。
 *
 * 二次确认才发请求（沿用 P5 的 notice warn + 两按钮样式，见 ReportReader）。
 * 成功之后核心就停了，界面不再发任何请求，只留在「正在退出」的静态提示上。
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'

type Stage = 'idle' | 'confirming' | 'closing'

interface Props {
  /** 提示回调：退出请求没生效时用它说清楚，而不是静默失败。 */
  onNotice?: (kind: 'ok' | 'error', text: string) => void
}

export function ExitButton({ onNotice }: Props) {
  const { t } = useTranslation()
  const [stage, setStage] = useState<Stage>('idle')

  const confirmExit = async () => {
    setStage('closing')
    try {
      await api.shutdown()
    } catch {
      // 核心已经停了也算成功（响应可能来不及回来），故只有明确失败才回退并提示
      setStage('idle')
      onNotice?.('error', t('desktop.exit_failed'))
    }
  }

  return (
    <>
      <button
        className="ghost"
        disabled={stage === 'closing'}
        onClick={() => setStage(stage === 'idle' ? 'confirming' : 'idle')}
      >
        {t('desktop.exit')}
      </button>
      {stage === 'confirming' && (
        <div className="topbar-confirm">
          <span>{t('desktop.exit_confirm')}</span>
          <button className="danger" onClick={() => void confirmExit()}>
            {t('desktop.exit_confirm_ok')}
          </button>
          <button className="ghost" onClick={() => setStage('idle')}>
            {t('desktop.exit_cancel')}
          </button>
        </div>
      )}
      {stage === 'closing' && <span className="muted">{t('desktop.exit')}…</span>}
    </>
  )
}
