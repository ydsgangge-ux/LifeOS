/** 报告阅读器（第 9.7 节）：周报月报的阅读、证据追溯、排除差异、小实验采纳。
 *
 * 正常报告与降级报告共用一个阅读器：
 *   - 正常报告按「章节名 → 结论数组」渲染，每条结论带证据强度徽标、可点击的依据、
 *     替代解释，以及（若有）小实验的「采纳」按钮；
 *   - 降级报告（output.degraded）直接铺指标/诊断/缺口三组本地生成的文字。
 *
 * 依据与排除前后差异都取自 report.facts：指标血缘里带被排除的条目与原因
 * （第 8.10 节），因此不需要额外端点（第 9.7 节）。
 */

import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '../lib/api'
import { resolveI18nRef } from '../i18n'
import { formatDateTime } from '../lib/format'
import { renderMessage, strengthLabel, verdictLabel, evidenceRefLabel } from '../lib/labels'
import type {
  Entry,
  Experiment,
  FactPackage,
  HistoryPoint,
  MetricLine,
  Report,
  ReportFinding,
  ReportListItem,
  ReportOutput,
} from '../lib/types'
import { ReadAloud } from './ReadAloud'

/** 报告类型：周报 / 月报 / 年报（第 9.1 节）。 */
type ReportKind = 'weekly' | 'monthly' | 'yearly'

interface Props {
  onError: (problem: unknown, fallback: string) => void
  onNotice: (text: string) => void
  /** 儿童档：报告可「读给我听」（P5 W5b）。 */
  readAloud?: boolean
  /** 档案年龄档（P5 W3）：child/teen 报告不自动产出，需监护人二次确认。 */
  ageBand: string
  /** 当前会话角色（P5 W3）：只有 guardian 会话能代孩子生成报告。 */
  role: string
}

/** 从报告 output 里挑出可朗读的正文（结论、本地降级文字、小实验）。 */
function collectSpeech(value: unknown, out: string[]): void {
  if (typeof value === 'string') {
    const text = value.trim()
    if (text) out.push(text)
    return
  }
  if (Array.isArray(value)) {
    for (const item of value) collectSpeech(item, out)
    return
  }
  if (value && typeof value === 'object') {
    for (const key of ['rendered_claim', 'claim', 'summary', 'text', 'action', 'success_criteria']) {
      if (key in value) collectSpeech((value as Record<string, unknown>)[key], out)
    }
  }
}

function reportSpeech(report: Report): string {
  const out: string[] = []
  collectSpeech(report.output ?? {}, out)
  return out.slice(0, 40).join('。')
}

/** 与后端 adopt_experiment 同序采集小实验项，保证采纳索引对得上。 */
function collectExperiments(output: ReportOutput): Experiment[] {
  const out: Experiment[] = []
  for (const value of Object.values(output)) {
    if (Array.isArray(value)) {
      for (const item of value) {
        if (
          item &&
          typeof item === 'object' &&
          'action' in item &&
          ('success_criteria' in item || 'window_days' in item)
        ) {
          out.push(item as Experiment)
        }
      }
    }
  }
  return out
}

/** 依据字段 → 指标行。支持 `metrics.<key>`、`<key>`、`<key>.<字段>` 三种写法。 */
function findMetric(facts: FactPackage | null, ref: string): MetricLine | null {
  if (!facts) return null
  const bare = ref.startsWith('metrics.') ? ref.slice('metrics.'.length) : ref
  const keys = facts.metrics.map((m) => m.key).sort((a, b) => b.length - a.length)
  for (const key of keys) {
    if (bare === key || bare.startsWith(`${key}.`)) {
      return facts.metrics.find((m) => m.key === key) ?? null
    }
  }
  return null
}

function metricText(metric: MetricLine, value: number | null): string {
  if (value === null) return '—'
  return `${value}${metric.unit ?? ''}`
}

function isFinding(item: unknown): item is ReportFinding {
  return typeof item === 'object' && item !== null && 'claim' in item
}

/** 多月趋势表的列（月报近 6 个月、年报整年 12 个月，第 9.1 节）。 */
type HistoryField = Exclude<keyof HistoryPoint, 'month'>
const HISTORY_COLUMNS: { field: HistoryField; key: string; percent?: boolean }[] = [
  { field: 'record_completeness', key: 'record_completeness', percent: true },
  { field: 'expense_total', key: 'expense_total' },
  { field: 'mood_mean', key: 'mood_mean' },
  { field: 'exercise_minutes', key: 'exercise_minutes' },
  { field: 'relation_entries', key: 'relation_entries' },
  { field: 'career_minutes_total', key: 'career_minutes_total' },
  { field: 'education_minutes_total', key: 'education_minutes_total' },
  { field: 'hobby_minutes_total', key: 'hobby_minutes_total' },
]

/** 逐月数字：缺失显示「—」，比例按百分比，其余按本地数字格式。 */
function historyCell(value: number | null | undefined, percent: boolean | undefined): string {
  if (value === null || value === undefined) return '—'
  if (percent) return `${(value * 100).toFixed(1)}%`
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)
}

export function ReportReader({ onError, onNotice, readAloud = false, ageBand, role }: Props) {
  const { t } = useTranslation()
  const [items, setItems] = useState<ReportListItem[]>([])
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [adopting, setAdopting] = useState<number | null>(null)
  const [openRef, setOpenRef] = useState<string | null>(null)
  const [entries, setEntries] = useState<Record<string, Entry | null>>({})
  /** 儿童/青少年档：待二次确认的生成请求（P5 W3）。 */
  const [confirmKind, setConfirmKind] = useState<ReportKind | null>(null)

  // 儿童/青少年档报告不自动产出：只有监护人代点并二次确认才生成（第 7.7 节、W3）。
  const childBand = ageBand === 'child' || ageBand === 'teen'
  const guardianSession = role === 'guardian'
  const canGenerate = !childBand || guardianSession

  const loadList = useCallback(async () => {
    setLoading(true)
    try {
      const page = await api.listReports()
      setItems(page.items)
    } catch (problem) {
      onError(problem, t('error.load_entries'))
    } finally {
      setLoading(false)
    }
  }, [onError, t])

  useEffect(() => {
    void loadList()
  }, [loadList])

  const openReport = async (id: string) => {
    setLoading(true)
    setOpenRef(null)
    setEntries({})
    try {
      setReport(await api.getReport(id))
    } catch (problem) {
      onError(problem, t('error.load_entries'))
    } finally {
      setLoading(false)
    }
  }

  /** 真正提交生成请求；儿童档带上监护人确认字段（P5 W3、S5）。 */
  const runGenerate = async (kind: ReportKind) => {
    setConfirmKind(null)
    setGenerating(true)
    try {
      const created = await api.createReport({
        kind,
        ...(childBand ? { confirm_child_report: true } : {}),
      })
      await loadList()
      setReport(created)
      onNotice(t('notice.saved'))
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setGenerating(false)
    }
  }

  /** 儿童/青少年档先弹二次确认，确认后再提交，避免系统悄悄替孩子产出报告。 */
  const generate = (kind: ReportKind) => {
    if (childBand) {
      setConfirmKind(kind)
      return
    }
    void runGenerate(kind)
  }

  const adopt = async (index: number) => {
    if (!report) return
    setAdopting(index)
    try {
      const result = await api.adoptExperiment(report.id, index)
      onNotice(t('reports.adopted', { title: result.plan.title }))
    } catch (problem) {
      onError(problem, t('error.save'))
    } finally {
      setAdopting(null)
    }
  }

  const openEntry = async (entryId: string) => {
    if (entries[entryId] !== undefined) {
      setOpenRef((current) => (current === entryId ? null : entryId))
      return
    }
    try {
      const entry = await api.getEntry(entryId)
      setEntries((previous) => ({ ...previous, [entryId]: entry }))
      setOpenRef(entryId)
    } catch {
      setEntries((previous) => ({ ...previous, [entryId]: null }))
      setOpenRef(entryId)
    }
  }

  if (loading && !report) {
    return (
      <div className="card">
        <p className="muted">{t('entry.loading')}</p>
      </div>
    )
  }

  if (!report) {
    return (
      <div className="card">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <h2>{t('reports.title')}</h2>
          {canGenerate && (
            <div className="row">
              <button
                className="primary"
                disabled={generating}
                onClick={() => generate('weekly')}
              >
                {generating ? t('reports.generating') : t('reports.weekly')}
              </button>
              <button disabled={generating} onClick={() => generate('monthly')}>
                {t('reports.monthly')}
              </button>
              <button disabled={generating} onClick={() => generate('yearly')}>
                {t('reports.yearly')}
              </button>
            </div>
          )}
        </div>
        <p className="muted">
          {childBand && !guardianSession
            ? t('report.child.gate_guardian_required')
            : t('reports.generate_hint')}
        </p>
        {confirmKind && (
          <div className="notice warn" style={{ marginBottom: 'var(--space-3)' }}>
            <div>{t('report.child.confirm_prompt')}</div>
            <div className="row" style={{ marginTop: 'var(--space-2)' }}>
              <button
                className="primary"
                disabled={generating}
                onClick={() => void runGenerate(confirmKind)}
              >
                {t('report.child.confirm_ok')}
              </button>
              <button
                className="ghost"
                disabled={generating}
                onClick={() => setConfirmKind(null)}
              >
                {t('report.child.confirm_cancel')}
              </button>
            </div>
          </div>
        )}
        {items.length === 0 && !loading && <p className="muted">{t('reports.empty')}</p>}
        {items.map((item) => (
          <div className="entry" key={item.id}>
            <div className="head">
              <span className="title">{t(`reports.kind.${item.kind}`)}</span>
              {/* created_at 存的是 UTC，按本机时区与界面语言排版，不能直接截串 */}
              <span className="when">{formatDateTime(item.created_at)}</span>
            </div>
            <div style={{ marginTop: 'var(--space-1)' }}>
              <span className={`tag ${item.status === 'degraded' ? 'anomaly' : 'domain'}`}>
                {t(`reports.status.${item.status}`, { defaultValue: item.status })}
              </span>
              {item.period_start && item.period_end && (
                <span className="tag">
                  {t('reports.period', { start: item.period_start, end: item.period_end })}
                </span>
              )}
            </div>
            <div className="row" style={{ marginTop: 'var(--space-2)' }}>
              <button className="ghost" onClick={() => void openReport(item.id)}>
                {t('reports.title')}
              </button>
            </div>
          </div>
        ))}
      </div>
    )
  }

  const output = report.output ?? {}
  const facts = report.facts
  const degraded = output.degraded === true
  // 儿童版报告（第 9.5 节、P5 W3）：结构同为降级报告，但章节名与说明换成孩子话。
  const childReport = output.child === true
  const sectionPrefix = childReport ? 'report.child' : 'report.degrade'
  const experiments = collectExperiments(output)
  const excludedMetrics = (facts?.metrics ?? []).filter(
    (m) => m.excluded_anomalies > 0,
  )
  // 多月趋势（第 9.1 节）：月报近 6 个月、年报整年；只显示真正有值的列
  const history = facts?.history ?? []
  const historyColumns = HISTORY_COLUMNS.filter((col) =>
    history.some((row) => row[col.field] !== null && row[col.field] !== undefined),
  )
  const headline = typeof output.headline === 'string' ? output.headline.trim() : ''
  /** 分领域 agent 的产出：每个有数据的领域一段小结，空领域只记名字不调用模型。 */
  const domainRows = Array.isArray(output.domains)
    ? (output.domains as Record<string, unknown>[])
    : []
  const skippedDomains = Array.isArray(output.no_data_domains)
    ? (output.no_data_domains as string[])
    : []
  const aiUsage =
    output.ai_usage && typeof output.ai_usage === 'object'
      ? (output.ai_usage as {
          calls?: number
          prompt_tokens?: number
          completion_tokens?: number
          cost_est?: number | null
          degraded_domains?: string[]
        })
      : null
  const domainLabel = (domain: string) => t(`domain.${domain}`, { defaultValue: domain })
  const costValue = aiUsage?.cost_est
  const costText =
    costValue === null || costValue === undefined
      ? t('reports.ai_usage_no_cost')
      : t('reports.ai_usage_cost', {
          cost: new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(costValue),
        })

  const renderFinding = (finding: ReportFinding) => (
    <div className="finding">
      <div className="finding-head">
        <span className="finding-claim">{finding.rendered_claim ?? finding.claim}</span>
        {finding.strength && (
          <span className={`tag strength strength-${finding.strength}`}>
            {strengthLabel(finding.strength)}
          </span>
        )}
        {finding.verdict && (
          <span className={`tag verdict verdict-${finding.verdict}`}>
            {verdictLabel(finding.verdict)}
          </span>
        )}
      </div>
      {finding.evidence_refs && finding.evidence_refs.length > 0 && (
        <details className="evidence-block">
          <summary className="muted">
            {t('reports.evidence_count', { count: finding.evidence_refs.length })}
          </summary>
          <div className="evidence-chips">
            {finding.evidence_refs.map((ref) => {
              const metric = findMetric(facts, ref)
              if (metric) {
                return (
                  <button
                    key={ref}
                    className="chip"
                    onClick={() => setOpenRef(openRef === ref ? null : ref)}
                  >
                    {resolveI18nRef(metric.description)}
                  </button>
                )
              }
              if (/^[0-9A-Z]{20,}$/.test(ref)) {
                return (
                  <button key={ref} className="chip" onClick={() => void openEntry(ref)}>
                    {t('reports.evidence_entry', { ref: ref.slice(-6) })}
                  </button>
                )
              }
              return (
                <span key={ref} className="chip static">
                  {evidenceRefLabel(ref) ?? t('reports.evidence_other')}
                </span>
              )
            })}
          </div>
        </details>
      )}
      {openRef && findMetric(facts, openRef) && (() => {
        const metric = findMetric(facts, openRef)!
        return (
          <div className="lineage">
            <div className="muted">{resolveI18nRef(metric.description)}</div>
            {metric.lineage.source && <div className="muted">{metric.lineage.source}</div>}
            {metric.lineage.excluded.length > 0 && (
              <details>
                <summary>
                  {t('reports.excluded_title')}（{metric.lineage.excluded.length}）
                </summary>
                {metric.lineage.excluded.map((item) => (
                  <div key={item.entry_id} className="muted">
                    {t('reports.excluded_entry', { date: item.date, reason: item.reason })}
                  </div>
                ))}
              </details>
            )}
          </div>
        )
      })()}
      {openRef && entries[openRef] && (
        <div className="lineage">
          <div>{entries[openRef]?.title || t('entry.no_title')}</div>
          <div className="muted">{entries[openRef]?.occurred_at.slice(0, 16).replace('T', ' ')}</div>
          {entries[openRef]?.note && <div className="detail">{entries[openRef]?.note}</div>}
        </div>
      )}
      {finding.alternatives && finding.alternatives.length > 0 && (
        <div className="detail">
          {t('reports.alternatives')}：{finding.alternatives.join('；')}
        </div>
      )}
      {finding.experiment && (
        <div className="detail">
          {finding.experiment.action}
          {finding.experiment.success_criteria ? `：${finding.experiment.success_criteria}` : ''}
        </div>
      )}
    </div>
  )

  const renderSection = (key: string) => {
    const value = output[key]
    if (key === 'adjustments' || key === 'experiments' || key === 'advice') {
      // 小实验数组：每项一个「采纳」按钮，索引与后端一致。
      // 月报/年报的建议层键名是 advice（分层摘要后不再叫 experiments）。
      const list = Array.isArray(value) ? value : []
      return (
        <div key={key}>
          <div className="section-title">{t(`report.section.${key}`, { defaultValue: key })}</div>
          {list.map((item, index) => {
            const experiment = item as Experiment
            const experimentIndex = experiments.indexOf(experiment)
            return (
              <div className="finding" key={index}>
                <div className="finding-claim">{experiment.action}</div>
                {experiment.success_criteria && (
                  <div className="detail">
                    {experiment.success_criteria}
                    {experiment.window_days ? `（验证窗口 ${experiment.window_days} 天）` : ''}
                  </div>
                )}
                <div className="row" style={{ marginTop: 'var(--space-2)' }}>
                  <button
                    disabled={adopting === experimentIndex}
                    onClick={() => void adopt(experimentIndex)}
                  >
                    {t('reports.adopt')}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )
    }
    if (Array.isArray(value)) {
      return (
        <div key={key}>
          <div className="section-title">{t(`report.section.${key}`, { defaultValue: key })}</div>
          {value.map((item, index) =>
            typeof item === 'string' ? (
              <div key={index} className="detail">
                {item}
              </div>
            ) : isFinding(item) ? (
              <div key={index}>{renderFinding(item)}</div>
            ) : null,
          )}
        </div>
      )
    }
    return null
  }

  // 降级报告的分支单独渲染三组本地文字，正常报告里这些键不会出现。
  const SECTION_SKIP = new Set([
    'headline',
    'degraded',
    'reason',
    'metrics',
    'domain_facts',
    'plans',
    'domains',
    'ai_usage',
    'no_data_domains',
  ])

  /** 空章节不占版面：模型偶尔会给出空数组或空字符串。 */
  const hasContent = (value: unknown): boolean => {
    if (Array.isArray(value)) return value.length > 0
    if (typeof value === 'string') return value.trim() !== ''
    return value !== null && value !== undefined
  }

  const speechText = reportSpeech(report)

  return (
    <div className="card">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <h2>
          {t(`reports.kind.${report.kind}`)} ·{' '}
          {report.period_start && report.period_end
            ? t('reports.period', { start: report.period_start, end: report.period_end })
            : ''}
        </h2>
        <button className="ghost" onClick={() => setReport(null)}>
          {t('reports.back')}
        </button>
      </div>
      <div style={{ marginBottom: 'var(--space-3)' }}>
        <span className={`tag ${report.status === 'degraded' ? 'anomaly' : 'domain'}`}>
          {t(`reports.status.${report.status}`, { defaultValue: report.status })}
        </span>
        {/* created_at 存的是 UTC，按本机时区与界面语言排版 */}
        <span className="tag">
          {t('reports.created_at', { date: formatDateTime(report.created_at) })}
        </span>
        {!degraded && report.model && (
          <span className="tag domain">
            {t('reports.ai_generated', { model: report.model })}
          </span>
        )}
        {degraded && <span className="tag">{t('reports.local_generated')}</span>}
        {report.metrics_version && (
          <span className="tag">
            {t('reports.provenance', {
              template: report.template_version ?? '—',
              metrics: report.metrics_version,
            })}
          </span>
        )}
        {aiUsage && aiUsage.calls ? (
          <span className="tag">
            {t('reports.ai_usage', {
              calls: aiUsage.calls,
              input: aiUsage.prompt_tokens ?? 0,
              output: aiUsage.completion_tokens ?? 0,
            })}
            {costText}
          </span>
        ) : null}
      </div>

      {readAloud && speechText && (
        <div style={{ marginBottom: 'var(--space-3)' }}>
          <ReadAloud text={speechText} lang="zh-CN" />
        </div>
      )}

      {report.metrics_hash && (
        <details>
          <summary>{t('reports.provenance_details')}</summary>
          <div className="muted">{t('reports.metrics_hash', { hash: report.metrics_hash })}</div>
        </details>
      )}

      {degraded && (
        <div className="notice warn">
          {childReport ? (
            t('report.child.notice')
          ) : (
            <>
              {renderMessage(output.reason ?? t('reports.degraded_banner'))}
              <div className="muted" style={{ marginTop: 'var(--space-1)' }}>
                {t('reports.degraded_banner')}
              </div>
            </>
          )}
        </div>
      )}

      {headline && (
        <div className="report-headline-block">
          <span className={`tag ${degraded ? '' : 'domain'}`}>
            {t(degraded ? 'reports.headline_local' : 'reports.headline_ai')}
          </span>
          <p className="report-headline">{headline}</p>
          {!degraded && <p className="muted">{t('reports.ai_note')}</p>}
        </div>
      )}

      {/* 分领域小结：每个有记录的领域一段，空领域不出段落（第 9.2 节） */}
      {!degraded && domainRows.length > 0 && (
        <div style={{ marginBottom: 'var(--space-3)' }}>
          <div className="section-title">{t('report.section.domains')}</div>
          {domainRows.map((row, index) => {
            const domain = String(row.domain ?? '')
            const findings = Array.isArray(row.findings)
              ? (row.findings as ReportFinding[])
              : []
            // 三层分开：简介是「发生了什么」，findings 是「怎么判断」，advice 是「下一步做什么」
            const advice = Array.isArray(row.advice)
              ? (row.advice as unknown[]).filter(
                  (item): item is string => typeof item === 'string' && item.trim() !== '',
                )
              : []
            const gaps = Array.isArray(row.gaps)
              ? (row.gaps as unknown[]).filter(
                  (item): item is string => typeof item === 'string' && item.trim() !== '',
                )
              : []
            return (
              <div key={`${domain}-${index}`} style={{ marginBottom: 'var(--space-3)' }}>
                <div className="finding-head">
                  <span className="finding-claim">{domainLabel(domain)}</span>
                  {row.degraded === true && (
                    <span className="tag">{t('report.domain.local_badge')}</span>
                  )}
                </div>
                {typeof row.summary === 'string' && row.summary.trim() !== '' && (
                  <p className="detail">{row.summary}</p>
                )}
                {findings.map((finding, findingIndex) => (
                  <div key={findingIndex}>{renderFinding(finding)}</div>
                ))}
                {advice.length > 0 && (
                  <div className="detail">
                    <div className="muted">{t('report.section.advice')}</div>
                    <ul>
                      {advice.map((item, itemIndex) => (
                        <li key={itemIndex}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {gaps.length > 0 && (
                  <div className="detail">
                    <div className="muted">{t('report.section.domain_gaps')}</div>
                    <ul>
                      {gaps.map((item, itemIndex) => (
                        <li key={itemIndex}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {!degraded && skippedDomains.length > 0 && (
        <p className="muted">
          {t('reports.no_data_domains', {
            domains: skippedDomains.map(domainLabel).join(' / '),
          })}
        </p>
      )}

      {/* 儿童版报告不出现百分数与完成率（第 9.5 节），因此不显示多月趋势表 */}
      {!childReport && history.length > 0 && historyColumns.length > 0 && (
        <div style={{ marginBottom: 'var(--space-3)' }}>
          <div className="section-title">{t('reports.history_title')}</div>
          <div style={{ overflowX: 'auto' }}>
            <table className="history-table">
              <thead>
                <tr>
                  <th>{t('reports.history.month')}</th>
                  {historyColumns.map((col) => (
                    <th key={col.key}>{t(`reports.history.${col.key}`)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.month}>
                    <td>{row.month}</td>
                    {historyColumns.map((col) => (
                      <td key={col.key}>{historyCell(row[col.field], col.percent)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted">{t('reports.history_hint')}</p>
        </div>
      )}

      {degraded ? (
        <>
          <div className="section-title">{t(`${sectionPrefix}.metrics_title`)}</div>
          {(output.metrics ?? []).map((row, index) => (
            <div key={index} className="detail">
              {row.text}
            </div>
          ))}
          {(output.domain_facts ?? []).length > 0 && (
            <>
              <div className="section-title">{t(`${sectionPrefix}.domains_title`)}</div>
              {(output.domain_facts ?? []).map((row, index) => (
                <div key={`${row.domain}-${index}`} className="detail">
                  {row.text}
                </div>
              ))}
            </>
          )}
          <div className="section-title">{t(`${sectionPrefix}.plans_title`)}</div>
          {(output.plans ?? []).map((row, index) => (
            <div key={index} className="detail">
              {row.text}
            </div>
          ))}
          <div className="section-title">{t(`${sectionPrefix}.gaps_title`)}</div>
          {(output.data_gaps ?? []).map((row, index) => (
            <div key={index} className="detail">
              {typeof row === 'string' ? renderMessage(row) : JSON.stringify(row)}
            </div>
          ))}
        </>
      ) : (
        Object.keys(output)
          .filter((key) => !SECTION_SKIP.has(key) && hasContent(output[key]))
          .map(renderSection)
      )}

      <div className="section-title">{t('reports.excluded_title')}</div>
      {excludedMetrics.length === 0 ? (
        <p className="muted">{t('reports.excluded_none')}</p>
      ) : (
        <>
          {facts && (
            <p className="muted">
              {t('reports.anomaly_summary', {
                count: facts.anomalies.count,
                impact: facts.anomalies.impact_sum,
              })}
            </p>
          )}
          {excludedMetrics.map((metric) => (
            <details key={metric.key}>
              <summary>{resolveI18nRef(metric.description)}</summary>
              <div className="detail">
                {t('reports.excluded_metric', {
                  name: resolveI18nRef(metric.description),
                  before: metricText(metric, metric.value_without_exclusion),
                  after: metricText(metric, metric.value),
                  count: metric.excluded_anomalies,
                })}
              </div>
              {metric.lineage.excluded.map((item) => (
                <div key={item.entry_id} className="muted">
                  {t('reports.excluded_entry', { date: item.date, reason: item.reason })}
                </div>
              ))}
            </details>
          ))}
        </>
      )}
    </div>
  )
}
