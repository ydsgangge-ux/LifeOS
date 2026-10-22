/** 加密备份与恢复（第 7.6 节、第 11.1 节、FR-17）。
 *
 * 三块内容：
 *   - 立刻打一份备份（可另存到别的目录，方便挪到外置硬盘/网盘）；
 *   - 本机备份列表，点一下就把路径填进恢复框；
 *   - 从备份恢复：覆盖前要先填档案别名确认（第二次确认由后端强制），
 *     口令或恢复密钥二者给一个即可。
 *
 * 恢复会先在服务端锁定当前会话（否则文件还被连接占着），所以恢复成功后
 * 必须回到解锁页——这里通过 onRestored 交回给外壳处理。
 */

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import type { BackupItem } from '../lib/types'

interface Props {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
  /** 恢复成功后调用：会话已被服务端锁定，需要回解锁页。 */
  onRestored: (text: string) => void
}

function fileName(path: string): string {
  const parts = path.split(/[\\/]/)
  return parts[parts.length - 1] || path
}

function toKb(bytes: number): number {
  return Math.round(bytes / 1024)
}

export function BackupSettings({ onError, onNotice, onRestored }: Props) {
  const { t } = useTranslation()
  const [items, setItems] = useState<BackupItem[]>([])
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [destDir, setDestDir] = useState('')
  const [path, setPath] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [recoveryKey, setRecoveryKey] = useState('')
  const [confirmAlias, setConfirmAlias] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const page = await api.listBackups()
      setItems(page.items)
    } catch (problem) {
      onError(problem, t('error.load_entries'))
    } finally {
      setLoading(false)
    }
  }, [onError, t])

  useEffect(() => {
    void load()
  }, [load])

  const create = async () => {
    setCreating(true)
    try {
      const result = await api.createBackup(destDir.trim() || undefined)
      onNotice(
        t('backup.created', {
          name: fileName(result.path),
          size: toKb(result.size_bytes),
        }),
      )
      await load()
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setCreating(false)
    }
  }

  const restore = async () => {
    setRestoring(true)
    try {
      const result = await api.restoreBackup({
        path: path.trim(),
        passphrase: passphrase || undefined,
        recovery_key: recoveryKey || undefined,
        confirm_alias: confirmAlias,
      })
      onRestored(t('backup.restored', { alias: result.alias }))
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setRestoring(false)
    }
  }

  const canRestore =
    path.trim() !== '' && (passphrase !== '' || recoveryKey !== '') && !restoring

  return (
    <div className="content single">
      <div className="card">
        <h2>{t('backup.title')}</h2>
        <p className="muted">{t('backup.hint')}</p>
        <div className="field-block">
          <label htmlFor="backup-dest">{t('backup.dest_label')}</label>
          <input
            id="backup-dest"
            value={destDir}
            placeholder={t('backup.dest_hint')}
            onChange={(event) => setDestDir(event.target.value)}
          />
        </div>
        <div className="row">
          <button className="primary" disabled={creating} onClick={() => void create()}>
            {creating ? t('backup.creating') : t('backup.create')}
          </button>
        </div>
      </div>

      <div className="card">
        <h2>{t('backup.list_title')}</h2>
        {loading && items.length === 0 && <p className="muted">{t('entry.loading')}</p>}
        {!loading && items.length === 0 && <p className="muted">{t('backup.empty')}</p>}
        {items.map((item) => (
          <div className="backup-row" key={item.path}>
            <div>
              {item.readable ? (
                <>
                  <div>{item.name}</div>
                  <div className="muted">
                    {t('backup.row', {
                      created: item.created_at.slice(0, 16).replace('T', ' '),
                      size: toKb(item.size_bytes),
                    })}
                  </div>
                </>
              ) : (
                <div className="muted">{t('backup.bad_row', { name: item.name })}</div>
              )}
            </div>
            <button
              className="ghost"
              disabled={!item.readable}
              onClick={() => setPath(item.path)}
            >
              {t('backup.use')}
            </button>
          </div>
        ))}
      </div>

      <div className="card">
        <h2>{t('backup.restore_title')}</h2>
        <p className="muted">{t('backup.restore_hint')}</p>
        <div className="field-block">
          <label htmlFor="backup-path">{t('backup.restore_path')}</label>
          <input
            id="backup-path"
            value={path}
            onChange={(event) => setPath(event.target.value)}
          />
          <label htmlFor="backup-passphrase">{t('backup.cred_passphrase')}</label>
          <input
            id="backup-passphrase"
            type="password"
            value={passphrase}
            onChange={(event) => setPassphrase(event.target.value)}
          />
          <label htmlFor="backup-recovery">{t('backup.cred_recovery')}</label>
          <input
            id="backup-recovery"
            value={recoveryKey}
            onChange={(event) => setRecoveryKey(event.target.value)}
          />
          <label htmlFor="backup-confirm">{t('backup.restore_alias')}</label>
          <input
            id="backup-confirm"
            value={confirmAlias}
            onChange={(event) => setConfirmAlias(event.target.value)}
          />
        </div>
        <div className="row">
          <button className="danger" disabled={!canRestore} onClick={() => void restore()}>
            {restoring ? t('backup.restoring') : t('backup.restore')}
          </button>
        </div>
      </div>
    </div>
  )
}
