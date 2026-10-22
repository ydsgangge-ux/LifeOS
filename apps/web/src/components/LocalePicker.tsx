/** 语言选择（NFR-08、P6 W4）：选项用各语言自己的写法，不翻译语言名。
 *
 * 切换是「界面先生效、档案后同步」：先切 i18next（立刻可见），再尽力写进档案；
 * 同步失败只给一条提示，不把用户卡在原语言上。
 */

import { useTranslation } from 'react-i18next'

import { getLocale } from '../i18n'
import { changeLocale, localeLabel, localeOptions } from '../lib/locale'

interface Props {
  /** 当前解锁的档案；还没解锁（解锁页）时传 null，此时只切界面、不落档案。 */
  profileId: string | null
  /** 提示回调（成功/失败都走这里），由外壳决定显示方式。 */
  onNotice?: (kind: 'ok' | 'error', text: string) => void
}

export function LocalePicker({ profileId, onNotice }: Props) {
  const { t } = useTranslation()

  const handleChange = async (code: string) => {
    const result = await changeLocale(code, profileId)
    if (!onNotice) return
    if (result.sync === 'failed') {
      // 注意：此刻界面已经是新语言，这条提示也要用新语言说
      onNotice('error', t('locale.sync_failed'))
    } else if (result.sync === 'ok') {
      onNotice('ok', t('locale.sync_ok', { language: localeLabel(result.locale) }))
    }
  }

  return (
    <label className="locale-picker" title={t('locale.description')}>
      <span className="muted">{t('locale.title')}</span>
      <select
        value={getLocale()}
        aria-label={t('locale.title')}
        onChange={(event) => void handleChange(event.target.value)}
      >
        {localeOptions().map((option) => (
          <option key={option.code} value={option.code}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}
