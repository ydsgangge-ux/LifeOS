/** 表单渲染器用到的纯函数（第 6 章）。
 *
 * 抽成纯函数是为了能在 Vitest 里直接测，不必起 DOM（第 13.3 节「表单渲染器」）。
 */

import type { FieldDef, FormDef } from './types'

/** 按年龄档裁剪字段（第 6.2 节：字段级 ages）。 */
export function visibleFields(form: FormDef, ageBand?: string): FieldDef[] {
  const out: FieldDef[] = []
  for (const section of form.sections) {
    for (const field of section.fields) {
      if (ageBand && field.ages && !field.ages.includes(ageBand)) continue
      out.push(field)
    }
  }
  return out
}

/** 按年龄档裁剪后仍非空的区块（空区块不渲染，避免界面上出现空标题）。
 *
 * 儿童档被映射成 `hidden` 控件（如 tags）的字段也一并剔除（第 6.2 节）。
 */
export function visibleSections(form: FormDef, ageBand?: string) {
  return form.sections
    .map((section) => ({
      id: section.id,
      fields: section.fields.filter(
        (field) =>
          !(ageBand && field.ages && !field.ages.includes(ageBand)) &&
          resolveWidget(field, ageBand) !== 'hidden',
      ),
    }))
    .filter((section) => section.fields.length > 0)
}

/** 控件形态（第 6.2 节「儿童档控件」）：把同一份 YAML 定义映射到不同年龄档的控件。 */
export type WidgetKind = 'default' | 'emoji5' | 'stepper' | 'icons' | 'file' | 'hidden'

/** 儿童档控件映射（第 6.2 节）。YAML 里 `widget.child` 可显式覆盖；
 *  未覆盖时按字段类型给儿童档一套更大、更直观的控件。
 *
 *  注意：`voice_first` 这类语音输入控件**不实现**（见 P5 计划 3.2：语音输入会把录音
 *  送到云端，与「儿童档不出站」冲突），因此它按类型回退到默认文本输入。
 */
export function resolveWidget(field: FieldDef, ageBand?: string): WidgetKind {
  const override = ageBand ? field.widget?.[ageBand] : undefined
  if (override === 'emoji_faces') return 'emoji5'
  if (override === 'coin_pad') return 'stepper'
  if (override === 'hidden') return 'hidden'
  if (ageBand !== 'child') return 'default'
  switch (field.type) {
    case 'tags':
      return 'hidden'
    case 'scale5':
      return 'emoji5'
    case 'number':
    case 'money':
    case 'duration':
      return 'stepper'
    case 'choice':
      return 'icons'
    case 'image':
      return 'file'
    default:
      return 'default'
  }
}

/** 儿童档步进控件的步长：忽略金额的 0.01 默认，统一按整数量增减（大号加减按钮）。 */
export function stepperStep(field: FieldDef): number {
  return field.step ?? 1
}

/** 只保留实际填了值的字段；空值一律不提交（避免写出一堆 NULL）。 */
export function pruneValues(values: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null) continue
    if (typeof value === 'string' && value.trim() === '') continue
    if (Array.isArray(value) && value.length === 0) continue
    out[key] = typeof value === 'string' ? value.trim() : value
  }
  return out
}

/** 数量类字段的取值范围与步长（第 6.3 节）。 */
export function numberBounds(field: FieldDef): { min?: number; max?: number; step: number } {
  const step = field.step ?? (field.type === 'money' ? 0.01 : field.type === 'scale5' ? 1 : 1)
  return {
    min: field.min ?? (field.type === 'scale5' ? 1 : undefined),
    max: field.max ?? (field.type === 'scale5' ? 5 : undefined),
    step,
  }
}
