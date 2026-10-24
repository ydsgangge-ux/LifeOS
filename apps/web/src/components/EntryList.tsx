/** 最简条目列表（第 11 章「今日页」的雏形）。
 *
 * 只做三件事：过滤、倒序展示最近条目、把意外单独标出来（第 5.2 节：
 * 意外始终可见，不因它丢掉条目）。
 */

import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'

import { DAY_CUTOFF_HOUR } from '../lib/day'
import { anomalyTypeLabel, domainLabel } from '../lib/labels'
import type { Entry, EntryFilters } from '../lib/types'
import { ReadAloud } from './ReadAloud'

const DOMAINS = [
  'daily',
  'income',
  'expense',
  'health',
  'goal',
  'plan',
  'relation',
  'education',
  'career',
  'hobby',
  'idea',
  'dream',
  'resource',
]

interface Props {
  entries: Entry[]
  loading: boolean
  filters: EntryFilters
  nextCursor: string | null
  onFilterChange: (filters: EntryFilters) => void
  onLoadMore: () => void
  onDelete: (entry: Entry) => void
  /** 时间线按天分组显示；录入页沿用平铺列表。 */
  groupByDay?: boolean
  /** 卡片标题；缺省「最近条目」。 */
  title?: string
  /** 儿童档：在条目标题下提供「读给我听」（P5 W5b）。 */
  readAloud?: boolean
  /** guardian 会话下被可见范围挡掉的条目数（P5 W7.3、验收 A2）：只报数，不显示标题。 */
  hiddenCount?: number
}

function when(entry: Entry): string {
  return entry.occurred_at.slice(0, 16).replace('T', ' ')
}

/** 凌晨记录（本地 0–4 点）按逻辑日归到前一天，行内仍显示真实时刻。
 *
 * 不标出来的话，分组头写 10-24、行内写 2026-10-25 01:30，看起来像对不上。
 * 判据与后端 util/ids.py 的 LOGICAL_DAY_CUTOFF_HOUR 相同。
 */
function crossedMidnight(entry: Entry): boolean {
  if (!entry.occurred_date) return false
  if (entry.occurred_at.slice(0, 10) === entry.occurred_date) return false
  return Number(entry.occurred_at.slice(11, 13)) < DAY_CUTOFF_HOUR
}

/** 详情字段压成一行摘要；空值不展示（第 4.1 节：字段全部可空）。 */
function detailSummary(entry: Entry): string {
  const parts: string[] = []
  for (const [key, value] of Object.entries(entry.detail ?? {})) {
    if (value === null || value === undefined || value === '') continue
    parts.push(`${key}=${Array.isArray(value) ? value.join('/') : String(value)}`)
    if (parts.length >= 5) break
  }
  return parts.join('  ·  ')
}

/** 朗读用的整段文字：标题 + 摘要 + 备注，用句号连起来（P5 W5b）。 */
function entrySpeech(entry: Entry): string {
  return [entry.title, detailSummary(entry), entry.note]
    .map((part) => (part ?? '').trim())
    .filter(Boolean)
    .join('。')
}

export function EntryList({
  entries,
  loading,
  filters,
  nextCursor,
  onFilterChange,
  onLoadMore,
  onDelete,
  groupByDay = false,
  title,
  readAloud = false,
  hiddenCount = 0,
}: Props) {
  const { t } = useTranslation()

  const groups = useMemo(() => {
    if (!groupByDay) return null
    const byDay = new Map<string, Entry[]>()
    for (const entry of entries) {
      const day = entry.occurred_date || entry.occurred_at.slice(0, 10)
      const bucket = byDay.get(day)
      if (bucket) bucket.push(entry)
      else byDay.set(day, [entry])
    }
    return [...byDay.entries()]
  }, [entries, groupByDay])

  const renderEntry = (entry: Entry) => (
    <div className="entry" key={entry.id}>
      <div className="head">
        <span className="title">
          {entry.title || detailSummary(entry) || t('entry.no_title')}
        </span>
        <span className="when">{when(entry)}</span>
      </div>
      <div style={{ marginTop: 'var(--space-1)' }}>
        <span className="tag domain">{domainLabel(entry.domain)}</span>
        {crossedMidnight(entry) && (
          <span className="tag">{t('entry.logical_day', { day: entry.occurred_date })}</span>
        )}
        {entry.status === 'draft' && <span className="tag">{t('entry.draft')}</span>}
        {entry.tags.map((tag) => (
          <span className="tag" key={tag}>
            #{tag}
          </span>
        ))}
        {entry.anomalies.map((anomaly) => (
          <span className="tag anomaly" key={anomaly.id}>
            {t('entry.anomaly_tag', {
              type: anomalyTypeLabel(anomaly.type),
              impact: anomaly.impact,
            })}
          </span>
        ))}
      </div>
      {entry.title && detailSummary(entry) && <div className="detail">{detailSummary(entry)}</div>}
      {entry.note && <div className="detail">{entry.note}</div>}
      {readAloud && entrySpeech(entry) && (
        <div style={{ marginTop: 'var(--space-2)' }}>
          <ReadAloud text={entrySpeech(entry)} lang="zh-CN" />
        </div>
      )}
      <div className="row" style={{ marginTop: 'var(--space-2)' }}>
        <button className="ghost" onClick={() => onDelete(entry)}>
          {t('entry.delete')}
        </button>
      </div>
    </div>
  )

  return (
    <div className="card">
      <h2>{title ?? t('entry.recent')}</h2>

      {hiddenCount > 0 && (
        <div className="notice warn" style={{ marginBottom: 'var(--space-3)' }}>
          {t('guardian.hidden.notice', { count: hiddenCount })}
        </div>
      )}

      <div className="grid-2" style={{ marginBottom: 'var(--space-3)' }}>
        <div>
          <label>{t('entry.domain')}</label>
          <select
            value={filters.domain ?? ''}
            onChange={(event) =>
              onFilterChange({ ...filters, domain: event.target.value || undefined })
            }
          >
            <option value="">{t('entry.all')}</option>
            {DOMAINS.map((domain) => (
              <option key={domain} value={domain}>
                {domainLabel(domain)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label>{t('entry.keyword')}</label>
          <input
            type="text"
            value={filters.q ?? ''}
            placeholder={t('timeline.search_hint')}
            onChange={(event) => onFilterChange({ ...filters, q: event.target.value || undefined })}
          />
        </div>
        <div>
          <label>{t('timeline.tag')}</label>
          <input
            type="text"
            value={filters.tag ?? ''}
            placeholder={t('timeline.tag_placeholder')}
            onChange={(event) =>
              onFilterChange({ ...filters, tag: event.target.value || undefined })
            }
          />
        </div>
        <div>
          <label>{t('entry.date_from')}</label>
          <input
            type="date"
            value={filters.date_from ?? ''}
            onChange={(event) =>
              onFilterChange({ ...filters, date_from: event.target.value || undefined })
            }
          />
        </div>
        <div>
          <label>{t('entry.date_to')}</label>
          <input
            type="date"
            value={filters.date_to ?? ''}
            onChange={(event) =>
              onFilterChange({ ...filters, date_to: event.target.value || undefined })
            }
          />
        </div>
      </div>

      <label className="row" style={{ cursor: 'pointer', marginBottom: 'var(--space-3)' }}>
        <input
          type="checkbox"
          style={{ width: 'auto' }}
          checked={filters.has_anomaly === true}
          onChange={(event) =>
            onFilterChange({
              ...filters,
              has_anomaly: event.target.checked ? true : undefined,
            })
          }
        />
        <span>{t('entry.only_anomaly')}</span>
      </label>

      {loading && <p className="muted">{t('entry.loading')}</p>}
      {!loading && entries.length === 0 && <p className="muted">{t('entry.empty')}</p>}

      {groups ? (
        <div>
          {groups.map(([day, dayEntries]) => (
            <div className="timeline-day" key={day}>
              <div className="timeline-day-label">
                {t('timeline.day_count', { count: dayEntries.length })} · {day}
              </div>
              {dayEntries.map(renderEntry)}
            </div>
          ))}
        </div>
      ) : (
        <div>{entries.map(renderEntry)}</div>
      )}

      {nextCursor && (
        <div style={{ marginTop: 'var(--space-3)' }}>
          <button onClick={onLoadMore} disabled={loading}>
            {t('entry.load_more')}
          </button>
        </div>
      )}
    </div>
  )
}
