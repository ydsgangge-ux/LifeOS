/** 关系网络（第 11.1 节「关系」页、P4）。
 *
 * 左：人物列表；中：力导向网络图（本地 SVG，无外部依赖）；右：人物详情。
 * 下方：待确认的规则建议，逐条确认或驳回。只有确认过的关联才进入网络。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import { layoutGraph } from '../lib/forceLayout'
import type {
  GraphData,
  LinkSuggestion,
  PageReview,
  Person,
  PersonDetail,
  RelationReviewAnalysis,
} from '../lib/types'
import { PageAiReview } from './PageAiReview'

const GRAPH_WIDTH = 520
const GRAPH_HEIGHT = 360
const NODE_RADIUS = 16

export function PeopleNetwork({
  onError,
  onNotice,
}: {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
}) {
  const { t } = useTranslation()
  const [graph, setGraph] = useState<GraphData>({ nodes: [], edges: [] })
  const [people, setPeople] = useState<Person[]>([])
  const [suggestions, setSuggestions] = useState<LinkSuggestion[]>([])
  const [detail, setDetail] = useState<PersonDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const [review, setReview] = useState<PageReview<RelationReviewAnalysis> | null>(null)
  const [reviewing, setReviewing] = useState(false)

  const load = useCallback(async () => {
    try {
      const [graphData, list, pending] = await Promise.all([
        api.getGraph(),
        api.listPeople(),
        api.listLinkSuggestions(),
      ])
      setGraph(graphData)
      setPeople(list.items)
      setSuggestions(pending.items)
    } catch (problem) {
      onError(problem, '无法读取关系数据')
    }
  }, [onError])

  useEffect(() => {
    void load()
  }, [load])

  // 上次解读单独取：监护人会话下这页本就不可见，失败时当没有解读
  useEffect(() => {
    api
      .getRelationAiReview()
      .then((result) => setReview(result.item))
      .catch(() => setReview(null))
  }, [])

  const layout = useMemo(
    () =>
      layoutGraph(
        graph.nodes.map((n) => n.id),
        graph.edges.map((e) => ({ src: e.src, dst: e.dst, weight: e.weight })),
        GRAPH_WIDTH,
        GRAPH_HEIGHT,
      ),
    [graph],
  )

  const openDetail = async (personId: string) => {
    try {
      setDetail(await api.getPersonDetail(personId))
    } catch (problem) {
      onError(problem, '无法读取人物详情')
    }
  }

  const runRules = async () => {
    setBusy(true)
    try {
      const result = await api.runLinkSuggest()
      onNotice(t('people.run_rules_done', { n: result.created }))
      await load()
    } catch (problem) {
      onError(problem, '生成建议失败')
    } finally {
      setBusy(false)
    }
  }

  const resolve = async (item: LinkSuggestion, accept: boolean) => {
    try {
      if (accept) {
        await api.confirmLink(item.id)
        onNotice(t('people.confirmed_ok'))
      } else {
        await api.rejectLink(item.id)
        onNotice(t('people.rejected_ok'))
      }
      await load()
    } catch (problem) {
      onError(problem, accept ? '确认失败' : '驳回失败')
    }
  }

  const runAiReview = async () => {
    setReviewing(true)
    try {
      const result = await api.aiReviewRelations()
      setReview(result)
      onNotice(result.status === 'ok' ? t('people.ai_done') : t('people.ai_degraded'))
    } catch (problem) {
      onError(problem, t('people.ai_failed'))
    } finally {
      setReviewing(false)
    }
  }

  const relationLabel = (relation: string) => t(`relation.${relation}`)
  const sourceLabel = (createdBy: string) => t(`people.source_${createdBy}`)

  return (
    <div className="content single">
      <div className="card">
        <h2>{t('people.title')}</h2>
        <p className="muted">{t('people.hint')}</p>

        <h3 style={{ marginTop: 'var(--space-4)' }}>{t('people.suggest_title')}</h3>
        <div className="row wrap">
          <button className="primary" onClick={() => void runRules()} disabled={busy}>
            {t('people.run_rules')}
          </button>
        </div>
        {suggestions.length === 0 ? (
          <p className="muted">{t('people.no_suggestions')}</p>
        ) : (
          <ul style={{ listStyle: 'none', padding: 0 }}>
            {suggestions.map((item) => (
              <li key={item.id} className="metric-card" style={{ marginBottom: 'var(--space-2)' }}>
                <div className="label">
                  {t('people.suggestion_pair', { src: item.src_label, dst: item.dst_label })}
                  {' · '}
                  {relationLabel(item.relation)}
                  {' · '}
                  {sourceLabel(item.created_by)}
                </div>
                <div className="row">
                  <button className="primary" onClick={() => void resolve(item, true)}>
                    {t('people.confirm')}
                  </button>
                  <button onClick={() => void resolve(item, false)}>{t('people.reject')}</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="card">
        {people.length === 0 ? (
          <p className="muted">{t('people.no_people')}</p>
        ) : (
          <div className="network-layout">
            <div className="network-aside">
              <h3>{t('people.list')}</h3>
              <ul style={{ listStyle: 'none', padding: 0 }}>
                {people.map((person) => (
                  <li key={person.id}>
                    <button
                      className={detail?.id === person.id ? 'primary' : ''}
                      onClick={() => void openDetail(person.id)}
                    >
                      {person.display_name}
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            <svg
              className="network-graph"
              viewBox={`0 0 ${GRAPH_WIDTH} ${GRAPH_HEIGHT}`}
              role="img"
              aria-label={t('people.title')}
            >
              {graph.edges.map((edge, i) => {
                const a = layout.positions[edge.src]
                const b = layout.positions[edge.dst]
                if (!a || !b) return null
                return (
                  <line
                    key={`${edge.src}-${edge.dst}-${i}`}
                    x1={a.x}
                    y1={a.y}
                    x2={b.x}
                    y2={b.y}
                    className="graph-edge"
                    strokeWidth={Math.min(1 + edge.weight, 4)}
                  />
                )
              })}
              {graph.nodes.map((node) => {
                const p = layout.positions[node.id]
                if (!p) return null
                return (
                  <g
                    key={node.id}
                    className="graph-node"
                    transform={`translate(${p.x}, ${p.y})`}
                    onClick={() => void openDetail(node.id)}
                  >
                    <circle r={NODE_RADIUS} className={detail?.id === node.id ? 'on' : ''} />
                    <text y={NODE_RADIUS + 14} textAnchor="middle">
                      {node.label}
                    </text>
                  </g>
                )
              })}
            </svg>

            <div className="network-aside">
              <h3>{t('people.detail')}</h3>
              {!detail ? (
                <p className="muted">{t('people.pick_hint')}</p>
              ) : (
                <>
                  <p className="label">{detail.display_name}</p>
                  {detail.relation_type && (
                    <p className="meta">
                      {t('people.relation_type')}：{detail.relation_type}
                    </p>
                  )}
                  {detail.closeness != null && (
                    <p className="meta">
                      {t('people.closeness')}：{detail.closeness}
                    </p>
                  )}
                  <h4>{t('people.connected')}</h4>
                  {detail.links.length === 0 ? (
                    <p className="muted">{t('people.no_links')}</p>
                  ) : (
                    <ul>
                      {detail.links.map((link) => (
                        <li key={link.id}>
                          {link.other_label ?? '—'} · {relationLabel(link.relation)}
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <h2>{t('people.ai_title')}</h2>
        <p className="muted">{t('people.ai_review_hint')}</p>
        <div className="row wrap" style={{ marginTop: 'var(--space-3)', alignItems: 'baseline' }}>
          <button
            className="primary"
            disabled={reviewing || people.length === 0}
            onClick={() => void runAiReview()}
          >
            {reviewing ? t('people.ai_reviewing') : t('people.ai_review')}
          </button>
          {people.length === 0 && <span className="muted">{t('people.ai_need_people')}</span>}
        </div>
        {review && (
          <PageAiReview
            titleKey="people.ai_result"
            review={review}
            headline={review.analysis?.headline}
            sections={[
              { key: 'people.ai_patterns', items: review.analysis?.patterns },
              { key: 'people.ai_attention', items: review.analysis?.attention },
            ]}
            texts={[{ key: 'people.ai_uncertainty', items: review.analysis?.uncertainty }]}
            experiments={[{ key: 'people.ai_suggestions', items: review.analysis?.suggestions }]}
          />
        )}
      </div>
    </div>
  )
}
