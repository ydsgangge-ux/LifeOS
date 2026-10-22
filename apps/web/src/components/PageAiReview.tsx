/** 页面级 AI 解读的展示（自我认知第 16 章 / 关系第 11.1 节）。
 *
 * 与决策页一致：ok 时按小节展示模型结论；degraded 时只说明原因，不编造解读。
 * 数值占位符已由后端渲染（rendered_claim），这里不做任何计算。
 */

import { useTranslation } from 'react-i18next'

import { renderMessage, strengthLabel } from '../lib/labels'
import type { Experiment, MessageObject, ReportFinding } from '../lib/types'

export interface ReviewFindingSection {
  key: string
  items?: ReportFinding[]
}

export interface ReviewTextSection {
  key: string
  items?: string[]
}

export interface ReviewExperimentSection {
  key: string
  items?: Experiment[]
}

interface Props {
  titleKey: string
  review: {
    status: 'ok' | 'degraded'
    model?: string
    reason?: MessageObject | string
  }
  headline?: string
  sections?: ReviewFindingSection[]
  texts?: ReviewTextSection[]
  experiments?: ReviewExperimentSection[]
}

export function PageAiReview({ titleKey, review, headline, sections, texts, experiments }: Props) {
  const { t } = useTranslation()
  const heading = (
    <div className="row wrap" style={{ alignItems: 'baseline' }}>
      <h3 style={{ marginTop: 'var(--space-4)' }}>{t(titleKey)}</h3>
      {review.model && (
        <span className="muted">
          {t('review.model')}：{review.model}
        </span>
      )}
    </div>
  )

  if (review.status !== 'ok') {
    return (
      <div style={{ marginTop: 'var(--space-4)' }}>
        {heading}
        <div className="reminder">
          {review.reason ? renderMessage(review.reason) : t('review.degraded')}
        </div>
      </div>
    )
  }

  const hasContent =
    Boolean(headline) ||
    (sections ?? []).some((section) => (section.items ?? []).length > 0) ||
    (texts ?? []).some((section) => (section.items ?? []).length > 0) ||
    (experiments ?? []).some((section) => (section.items ?? []).length > 0)

  return (
    <div style={{ marginTop: 'var(--space-4)' }}>
      {heading}
      {!hasContent && <p className="muted">{t('review.empty')}</p>}
      {headline && <p className="report-headline">{headline}</p>}

      {(sections ?? []).map((section) =>
        (section.items ?? []).length > 0 ? (
          <div key={section.key}>
            <h4>{t(section.key)}</h4>
            <ul style={{ listStyle: 'none', padding: 0 }}>
              {(section.items ?? []).map((finding, index) => (
                <li key={index} className="metric-card" style={{ marginBottom: 'var(--space-2)' }}>
                  <div className="label">{finding.rendered_claim ?? finding.claim}</div>
                  {finding.strength && (
                    <div className="meta">
                      <span className={`tag strength strength-${finding.strength}`}>
                        {strengthLabel(finding.strength)}
                      </span>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null,
      )}

      {(texts ?? []).map((section) =>
        (section.items ?? []).length > 0 ? (
          <div key={section.key}>
            <h4>{t(section.key)}</h4>
            <ul>
              {(section.items ?? []).map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null,
      )}

      {(experiments ?? []).map((section) =>
        (section.items ?? []).length > 0 ? (
          <div key={section.key}>
            <h4>{t(section.key)}</h4>
            {(section.items ?? []).map((item, index) => (
              <div key={index} className="metric-card" style={{ marginBottom: 'var(--space-2)' }}>
                <div className="label">{item.action}</div>
                {item.success_criteria && <div className="meta">{item.success_criteria}</div>}
              </div>
            ))}
          </div>
        ) : null,
      )}
    </div>
  )
}
