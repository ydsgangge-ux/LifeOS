/** 仪表盘（第 11.1 节、第 11.4 节无障碍）。
 *
 * 只从聚合后的 /metrics 取数，从不加载原始条目：十万条记录也不影响渲染，
 * 卡片与图表都维持在常数级节点。图表只用主题 token 色并提供数据表替代。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import { logicalDate } from '../lib/day'
import { metricStatusLabel } from '../lib/labels'
import { resolveI18nRef } from '../i18n'
import type { MetricLine } from '../lib/types'
import { TrendChart, type TrendPoint } from './TrendChart'

type PeriodKind = 'weekly' | 'monthly'

/** 趋势取最近 4 期：本地聚合查询，点数固定，不随数据量增长。 */
const TREND_PERIODS = 4

function formatValue(value: number | null): string {
  if (value === null) return '—'
  if (Number.isInteger(value)) return String(value)
  return value.toFixed(Math.abs(value) < 1 ? 3 : 2)
}

function deltaClass(delta: number | null): 'up' | 'down' | 'flat' {
  if (delta === null || delta === 0) return 'flat'
  return delta > 0 ? 'up' : 'down'
}

export function Dashboard({
  onError,
}: {
  onError: (problem: unknown, fallback: string) => void
}) {
  const { t } = useTranslation()
  const [period, setPeriod] = useState<PeriodKind>('weekly')
  const [metrics, setMetrics] = useState<MetricLine[]>([])
  const [range, setRange] = useState<{ start: string; end: string } | null>(null)
  const [loading, setLoading] = useState(false)
  const [trendKey, setTrendKey] = useState('')
  const [trend, setTrend] = useState<TrendPoint[]>([])

  const load = useCallback(
    async (kind: PeriodKind) => {
      setLoading(true)
      try {
        const result = await api.listMetrics(kind)
        setMetrics(result.items)
        setRange({ start: result.period.start, end: result.period.end })
      } catch (problem) {
        onError(problem, t('dashboard.metric_failed'))
      } finally {
        setLoading(false)
      }
    },
    [onError, t],
  )

  useEffect(() => {
    void load(period)
  }, [period, load])

  const trendable = useMemo(() => metrics.filter((m) => m.value !== null), [metrics])

  useEffect(() => {
    if (trendable.length === 0) {
      setTrendKey('')
      return
    }
    if (!trendable.some((m) => m.key === trendKey)) {
      setTrendKey(trendable[0].key)
    }
  }, [trendable, trendKey])

  useEffect(() => {
    if (!trendKey) {
      setTrend([])
      return
    }
    let cancelled = false
    const stepDays = period === 'monthly' ? 30 : 7
    const loadTrend = async () => {
      try {
        // 只取当前指标、4 期并行：避免串行等待，也避免为 1 个指标算全部 15 个。
        const offsets = Array.from({ length: TREND_PERIODS }, (_, i) => TREND_PERIODS - 1 - i)
        const results = await Promise.all(
          offsets.map((i) => api.listMetrics(period, logicalDate(i * stepDays), [trendKey])),
        )
        const points: TrendPoint[] = []
        for (const result of results) {
          const line = result.items.find((m) => m.key === trendKey)
          if (line && line.value !== null) {
            points.push({ label: result.period.start.slice(5), value: line.value })
          }
        }
        if (!cancelled) setTrend(points)
      } catch (problem) {
        if (!cancelled) onError(problem, t('dashboard.metric_failed'))
      }
    }
    void loadTrend()
    return () => {
      cancelled = true
    }
  }, [trendKey, period, onError, t])

  const selected = metrics.find((m) => m.key === trendKey) ?? null

  return (
    <div className="content single">
      <div className="card">
        <h2>{t('dashboard.title')}</h2>
        <p className="muted">{t('dashboard.hint')}</p>

        <div className="row wrap" style={{ marginTop: 'var(--space-3)' }}>
          <span className="muted">{t('dashboard.period')}</span>
          {(['weekly', 'monthly'] as PeriodKind[]).map((kind) => (
            <button
              key={kind}
              className={kind === period ? 'primary' : ''}
              onClick={() => setPeriod(kind)}
            >
              {t(`dashboard.period.${kind}`)}
            </button>
          ))}
          {range && (
            <span className="muted">
              {t('dashboard.range', { start: range.start, end: range.end })}
            </span>
          )}
        </div>
      </div>

      {loading && <div className="card">{t('dashboard.metric_loading')}</div>}

      {!loading && metrics.length === 0 && (
        <div className="card empty-state">
          <div className="title">{t('dashboard.empty')}</div>
        </div>
      )}

      {!loading && metrics.length > 0 && (
        <>
          <div className="metric-grid" style={{ marginTop: 'var(--space-4)' }}>
            {metrics.map((metric) => (
              <div className="metric-card" key={metric.key}>
                <div className="label">{resolveI18nRef(metric.description)}</div>
                <div className="value">
                  {formatValue(metric.value)}
                  {metric.unit ? ` ${metric.unit}` : ''}
                </div>
                <div className="meta">
                  {metric.delta !== null && (
                    <span className={`delta ${deltaClass(metric.delta)}`}>
                      {metric.delta === 0
                        ? t('dashboard.delta_flat')
                        : t(metric.delta > 0 ? 'dashboard.delta_up' : 'dashboard.delta_down', {
                            delta: formatValue(Math.abs(metric.delta)),
                          })}
                    </span>
                  )}
                  {metric.delta !== null && ' · '}
                  {t('dashboard.n_samples', { n: metric.n })} · {metricStatusLabel(metric.status)}
                </div>
              </div>
            ))}
          </div>

          <div className="card" style={{ marginTop: 'var(--space-4)' }}>
            <h2>{t('dashboard.trend_title')}</h2>
            <p className="muted">{t('dashboard.chart_hint')}</p>
            {trendable.length > 0 && (
              <div className="row wrap" style={{ marginTop: 'var(--space-3)' }}>
                <label htmlFor="trend-metric" style={{ margin: 0 }}>
                  {t('dashboard.chart_title')}
                </label>
                <select
                  id="trend-metric"
                  value={trendKey}
                  onChange={(event) => setTrendKey(event.target.value)}
                  style={{ width: 'auto' }}
                >
                  {trendable.map((metric) => (
                    <option key={metric.key} value={metric.key}>
                      {resolveI18nRef(metric.description)}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <TrendChart points={trend} unit={selected?.unit ?? null} />
          </div>
        </>
      )}
    </div>
  )
}
