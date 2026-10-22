/** 界面文案（第 6.1 节）：全部走 i18n，键在仓库根 i18n/<locale>.yaml。
 *
 * 表单 YAML 的标题写成 `i18n:form.daily.title`；字段 / 领域 / 意外类型 /
 * 排除方式 / 年龄档的键按 field. / domain. / anomaly_type. / exclusion. /
 * age_band. 前缀组织。查不到的键原样显示，不做静默兜底，方便发现遗漏。
 */

import { resolveI18nRef, t } from '../i18n'
import type { MessageObject } from './types'

/** 表单标题：直接解析 YAML 里的 `i18n:` 引用。 */
export function formTitle(raw: string): string {
  return resolveI18nRef(raw)
}

export function fieldLabel(key: string): string {
  return t(`field.${key}`, { defaultValue: key })
}

export function domainLabel(domain: string): string {
  return t(`domain.${domain}`, { defaultValue: domain })
}

export function anomalyTypeLabel(type: string): string {
  return t(`anomaly_type.${type}`, { defaultValue: type })
}

export function exclusionModeLabel(mode: string): string {
  return t(`exclusion.${mode}`, { defaultValue: mode })
}

export function ageBandLabel(band: string): string {
  return t(`age_band.${band}`, { defaultValue: band })
}

/** 后端生成的文案是 {key, params} 消息对象（NFR-08），在这里本地化。 */
export function renderMessage(message: MessageObject | string): string {
  if (typeof message === 'string') return resolveI18nRef(message)
  return t(message.key, { ...message.params, defaultValue: message.key })
}

function pick(prefix: string, value: string | null | undefined): string {
  if (!value) return ''
  return t(`${prefix}.${value}`, { defaultValue: value })
}

export function goalStatusLabel(status: string): string {
  return pick('goals.status', status)
}

export function planStatusLabel(status: string): string {
  return pick('goals.plan_status', status)
}

export function cadenceLabel(cadence: string | null | undefined): string {
  return pick('goals.cadence', cadence)
}

export function horizonLabel(horizon: string | null | undefined): string {
  return pick('goals.horizon', horizon)
}

export function directionLabel(direction: string | null | undefined): string {
  return pick('goals.direction', direction)
}

export function decisionLabel(decision: string): string {
  return pick('goals.decision', decision)
}

/** 报告结论的证据强度徽标（第 9.7 节）。 */
export function strengthLabel(strength: string): string {
  return pick('reports.strength', strength)
}

/** 诊断去留判定（复用 report.verdict.* 词条）。 */
export function verdictLabel(verdict: string): string {
  return pick('report.verdict', verdict)
}

export function policyLabel(policy: string): string {
  return pick('egress.policy', policy)
}

/** 自我认知校验：自述类型 / 自述状态 / 假设状态 / 证据强度 / 判断结果（第 16 章）。 */
export function claimKindLabel(kind: string): string {
  return pick('selfcheck.kind', kind)
}

export function claimStatusLabel(status: string): string {
  return pick('selfcheck.status', status)
}

export function hypothesisStatusLabel(status: string): string {
  return pick('selfcheck.hypothesis_status', status)
}

export function hypothesisStrengthLabel(strength: string): string {
  return pick('selfcheck.strength', strength)
}

export function judgmentOutcomeLabel(outcome: string): string {
  return pick('selfcheck.outcome', outcome)
}

/** 检测器事实 / 覆盖项 / 缺口 / 证据说明的 i18n 键 → 文案。 */
export function selfCheckKeyLabel(key: string): string {
  return t(key, { defaultValue: key })
}

export function egressLogStatusLabel(status: string | null): string {
  return pick('egress.log_status', status)
}

/** 指标就绪状态（仪表盘卡片）：ok / insufficient / no_data（第 8 章）。 */
export function metricStatusLabel(status: string): string {
  return pick('dashboard.status', status)
}

/** 依据字段的字段名 → 人话；认不出的字段回落到「其他事实」，不把键名摊给用户。 */
function evidenceFieldLabel(field: string): string {
  return t(`reports.field.${field}`, { defaultValue: t('reports.field.other') })
}

/** 报告依据字段 → 人话标签（第 9.7 节）。
 *
 * 指标由阅读器用指标名渲染；这里负责 coverage / domain_facts / plans / anomalies /
 * history 这些没有指标名的字段。返回 null 表示认不出，调用方用兜底文案。
 */
export function evidenceRefLabel(ref: string): string | null {
  if (ref.startsWith('coverage.')) {
    return t('reports.evidence_coverage', {
      domain: domainLabel(ref.slice('coverage.'.length)),
    })
  }
  if (ref.startsWith('domain_facts.')) {
    const [, domain, ...rest] = ref.split('.')
    if (domain && rest.length > 0) {
      return t('reports.evidence_domain', {
        domain: domainLabel(domain),
        field: evidenceFieldLabel(rest.join('.')),
      })
    }
  }
  if (ref.startsWith('plans.')) {
    const [, plan, ...rest] = ref.split('.')
    if (plan && rest.length > 0) {
      const code = /^plan_([A-Z])$/.exec(plan)
      return t('reports.evidence_plan', {
        plan: code ? t('reports.plan_code', { code: code[1] }) : plan,
        field: evidenceFieldLabel(rest.join('.')),
      })
    }
  }
  if (ref.startsWith('anomalies.')) {
    return t('reports.evidence_anomaly', {
      field: evidenceFieldLabel(ref.slice('anomalies.'.length)),
    })
  }
  if (ref.startsWith('history.')) {
    const field = ref.split('.').pop() ?? ''
    return t('reports.evidence_history', { field: evidenceFieldLabel(field) })
  }
  return null
}

/** 结构化提交里指标/条目引用：`metric.key` → 指标名，`entry_id` → 原样。 */
export function evidenceLabel(ref: string): string {
  if (ref.startsWith('metrics.')) {
    const key = ref.slice('metrics.'.length)
    return t('reports.evidence_metric', { ref: t(`metric.${key}`, { defaultValue: key }) })
  }
  if (ref.startsWith('plan_')) return t('reports.evidence_ref', { ref })
  if (/^[0-9A-Z]{20,}$/.test(ref)) return t('reports.evidence_entry', { ref })
  return t('reports.evidence_ref', { ref })
}
