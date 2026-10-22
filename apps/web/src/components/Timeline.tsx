/** 时间线页（第 11.1 节）：全部领域按时间流动，支持筛选与全文检索。
 *
 * 查询交给后端（第 12.2 节的 GET /entries：domain / 日期 / q / tag / has_anomaly），
 * 前端只负责把筛选条件与游标传递下去、把结果按天分组呈现（第 5.2 节：意外始终可见）。
 */

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { EntryList } from './EntryList'
import { api } from '../lib/api'
import type { Entry, EntryFilters } from '../lib/types'

const DEFAULT_FILTERS: EntryFilters = { limit: 50 }

interface Props {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}

export function Timeline({ onError, onNotice }: Props) {
  const { t } = useTranslation()
  const [filters, setFilters] = useState<EntryFilters>(DEFAULT_FILTERS)
  const [entries, setEntries] = useState<Entry[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(
    async (next: EntryFilters, append: boolean) => {
      setLoading(true)
      try {
        const page = await api.listEntries(next)
        setEntries((previous) => (append ? [...previous, ...page.items] : page.items))
        setNextCursor(page.next_cursor)
      } catch (problem) {
        onError(problem, t('error.load_entries'))
      } finally {
        setLoading(false)
      }
    },
    [onError, t],
  )

  useEffect(() => {
    void load(filters, false)
  }, [filters, load])

  const handleDelete = async (entry: Entry) => {
    try {
      await api.deleteEntry(entry.id)
      setEntries((previous) => previous.filter((item) => item.id !== entry.id))
      onNotice(t('notice.deleted'))
    } catch (problem) {
      onError(problem, t('error.delete'))
    }
  }

  return (
    <EntryList
      title={t('nav.timeline')}
      groupByDay
      entries={entries}
      loading={loading}
      filters={filters}
      nextCursor={nextCursor}
      onFilterChange={(next) => {
        setNextCursor(null)
        setFilters(next)
      }}
      onLoadMore={() => {
        if (nextCursor) void load({ ...filters, cursor: nextCursor }, true)
      }}
      onDelete={(entry) => void handleDelete(entry)}
    />
  )
}
