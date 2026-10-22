/** 因果链（第 11.1 节「因果链」页、第 4.5 节、P4）。
 *
 * 从一条记录出发，沿**已确认**的 entry→entry 关联展开前因后果。
 * 后端最多展开 6 层并带环路保护，这里按层呈现。
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import type { ChainData, ChainNode, Entry } from '../lib/types'

export function ChainView({
  onError,
}: {
  onError: (problem: unknown, fallback: string) => void
}) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Entry[]>([])
  const [chain, setChain] = useState<ChainData | null>(null)
  const [depth, setDepth] = useState(6)
  const [busy, setBusy] = useState(false)

  const search = async () => {
    if (!query.trim()) return
    setBusy(true)
    try {
      const page = await api.listEntries({ q: query.trim(), limit: 10 })
      setResults(page.items)
      setChain(null)
    } catch (problem) {
      onError(problem, '搜索记录失败')
    } finally {
      setBusy(false)
    }
  }

  const open = async (entry: Entry) => {
    try {
      setChain(await api.getChain(entry.id, depth))
    } catch (problem) {
      onError(problem, '无法展开因果链')
    }
  }

  const nodeById = new Map((chain?.nodes ?? []).map((n) => [n.id, n]))
  const layers = new Map<number, ChainNode[]>()
  if (chain) {
    for (const node of chain.nodes) {
      const bucket = layers.get(node.depth) ?? []
      bucket.push(node)
      layers.set(node.depth, bucket)
    }
  }

  return (
    <div className="content single">
      <div className="card">
        <h2>{t('chain.title')}</h2>
        <p className="muted">{t('chain.hint')}</p>

        <div className="row wrap" style={{ marginTop: 'var(--space-3)' }}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('chain.search_placeholder')}
            style={{ flex: 1, minWidth: 240 }}
          />
          <label>
            {t('chain.depth')}
            <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
              {[1, 2, 3, 4, 5, 6].map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </label>
          <button className="primary" onClick={() => void search()} disabled={busy}>
            {t('chain.search')}
          </button>
        </div>

        {results.length > 0 && (
          <ul style={{ listStyle: 'none', padding: 0 }}>
            {results.map((entry) => (
              <li key={entry.id} className="metric-card" style={{ marginBottom: 'var(--space-2)' }}>
                <div className="label">{entry.title ?? entry.note ?? '—'}</div>
                <div className="meta">{entry.occurred_date}</div>
                <button onClick={() => void open(entry)}>{t('chain.open')}</button>
              </li>
            ))}
          </ul>
        )}

        {!chain ? (
          <p className="muted">{results.length === 0 ? t('chain.pick') : ''}</p>
        ) : (
          <div style={{ marginTop: 'var(--space-4)' }}>
            {chain.reachable_depth === 0 ? (
              <p className="muted">{t('chain.empty')}</p>
            ) : (
              <>
                <p className="meta">{t('chain.reachable', { n: chain.reachable_depth })}</p>
                {[...layers.keys()].sort((a, b) => a - b).map((level) => (
                  <div key={level} className="chain-layer">
                    <div className="chain-layer-title">
                      {level === 0 ? t('chain.root') : t('chain.layer', { n: level })}
                    </div>
                    <ul>
                      {(layers.get(level) ?? []).map((node) => (
                        <li key={node.id}>
                          {node.label}
                          {node.occurred_date ? ` · ${node.occurred_date}` : ''}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}

                <h4>{t('people.connected')}</h4>
                <ul>
                  {chain.edges.map((edge) => (
                    <li key={edge.id}>
                      {nodeById.get(edge.src)?.label ?? edge.src}
                      {' —'}
                      {t(`relation.${edge.relation}`)}
                      {'→ '}
                      {nodeById.get(edge.dst)?.label ?? edge.dst}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
