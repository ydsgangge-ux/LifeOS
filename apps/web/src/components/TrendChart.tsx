/** 趋势图（第 11.1 节仪表盘、第 11.4 节无障碍）。
 *
 * 只用主题 token 色（.bar-N 走 --chart-N），并提供「查看数据表」的文字替代，
 * 保证不看图也能拿到同样的信息。
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export interface TrendPoint {
  label: string
  value: number
}

const WIDTH = 320
const HEIGHT = 160
const PAD_X = 28
const PAD_Y = 20

export function TrendChart({ points, unit }: { points: TrendPoint[]; unit?: string | null }) {
  const { t } = useTranslation()
  const [showTable, setShowTable] = useState(false)

  if (points.length < 2) {
    return <p className="muted">{t('dashboard.trend_empty')}</p>
  }

  const values = points.map((p) => p.value)
  const max = Math.max(...values)
  const min = Math.min(...values)
  const span = max - min || Math.max(Math.abs(max), 1)
  const innerW = WIDTH - PAD_X * 2
  const innerH = HEIGHT - PAD_Y * 2
  const slot = innerW / points.length
  const barW = slot * 0.55

  const heightOf = (value: number): number => ((value - min) / span) * innerH

  return (
    <div>
      <svg className="chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-hidden="true">
        <line className="axis" x1={PAD_X} y1={HEIGHT - PAD_Y} x2={WIDTH - PAD_X} y2={HEIGHT - PAD_Y} />
        {points.map((point, index) => {
          const h = heightOf(point.value)
          const x = PAD_X + index * slot + (slot - barW) / 2
          const y = HEIGHT - PAD_Y - h
          return (
            <g key={point.label}>
              <rect
                className={`bar-${(index % 6) + 1}`}
                x={x}
                y={y}
                width={barW}
                height={Math.max(h, 1)}
                rx={3}
              />
              <text x={PAD_X + index * slot + slot / 2} y={HEIGHT - 6} textAnchor="middle">
                {point.label}
              </text>
            </g>
          )
        })}
      </svg>

      <button className="ghost" onClick={() => setShowTable((v) => !v)}>
        {showTable ? t('dashboard.hide_table') : t('dashboard.show_table')}
      </button>

      {showTable && (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t('dashboard.table_metric')}</th>
              <th>{t('dashboard.table_value')}</th>
            </tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.label}>
                <td>{point.label}</td>
                <td>
                  {formatNumber(point.value)}
                  {unit ? ` ${unit}` : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function formatNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(2)
}
