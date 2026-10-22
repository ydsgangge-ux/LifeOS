/** 表单渲染器的纯函数测试（第 13.3 节「前端单元 · 表单渲染器」）。 */

import { describe, expect, it } from 'vitest'

import { resolveOptions } from './dictionaries'
import { numberBounds, pruneValues, resolveWidget, stepperStep, visibleFields, visibleSections } from './formUtils'
import type { FieldDef, FormDef } from './types'

function field(key: string, over: Partial<FieldDef> = {}): FieldDef {
  return {
    key,
    type: 'text',
    target: null,
    ages: null,
    widget: {},
    unit: null,
    options: null,
    show_if: null,
    sensitivity_default: null,
    min: null,
    max: null,
    step: null,
    ...over,
  }
}

const form: FormDef = {
  id: 'expense.spend',
  domain: 'expense',
  version: 1,
  title: 'i18n:form.expense.title',
  anomaly: true,
  sections: [
    {
      id: 'amount',
      fields: [
        field('amount', { type: 'money', unit: '元' }),
        field('category', { type: 'choice', options: 'dict:expense_category' }),
        field('currency', { type: 'choice', options: ['CNY', 'JPY'] }),
      ],
    },
    {
      id: 'context',
      fields: [field('person', { type: 'person', ages: ['teen', 'adult', 'senior'] }), field('note')],
    },
  ],
}

describe('visibleFields / visibleSections', () => {
  it('字段级 ages 会按年龄档裁剪', () => {
    expect(visibleFields(form)).toHaveLength(5)
    expect(visibleFields(form, 'adult').map((f) => f.key)).toContain('person')
    expect(visibleFields(form, 'child').map((f) => f.key)).not.toContain('person')
  })

  it('裁剪后只剩空字段的区块不渲染', () => {
    const sections = visibleSections(form, 'child')
    expect(sections.map((s) => s.id)).toEqual(['amount', 'context'])
    expect(sections[1].fields.map((f) => f.key)).toEqual(['note'])

    const personOnly: FormDef = {
      ...form,
      sections: [{ id: 'who', fields: [field('person', { ages: ['adult'] })] }],
    }
    expect(visibleSections(personOnly, 'child')).toHaveLength(0)
  })

  it('儿童档把 tags 映射成 hidden 并从区块里剔除', () => {
    const withTags: FormDef = {
      ...form,
      sections: [{ id: 'tags', fields: [field('tags', { type: 'tags' })] }],
    }
    expect(visibleSections(withTags, 'child')).toHaveLength(0)
    // 非儿童档仍照常渲染
    expect(visibleSections(withTags, 'adult')).toHaveLength(1)
  })
})

describe('resolveWidget', () => {
  it('儿童档按字段类型换控件（第 6.2 节）', () => {
    expect(resolveWidget(field('mood', { type: 'scale5' }), 'child')).toBe('emoji5')
    expect(resolveWidget(field('n', { type: 'number' }), 'child')).toBe('stepper')
    expect(resolveWidget(field('m', { type: 'money' }), 'child')).toBe('stepper')
    expect(resolveWidget(field('d', { type: 'duration' }), 'child')).toBe('stepper')
    expect(resolveWidget(field('c', { type: 'choice' }), 'child')).toBe('icons')
    expect(resolveWidget(field('t', { type: 'tags' }), 'child')).toBe('hidden')
    expect(resolveWidget(field('pic', { type: 'image' }), 'child')).toBe('file')
    expect(resolveWidget(field('note'), 'child')).toBe('default')
  })

  it('未知类型在儿童档回退默认控件', () => {
    expect(resolveWidget(field('x', { type: 'mystery' }), 'child')).toBe('default')
  })

  it('非儿童档一律默认控件', () => {
    for (const band of ['teen', 'adult', 'senior', undefined]) {
      expect(resolveWidget(field('mood', { type: 'scale5' }), band)).toBe('default')
    }
  })

  it('YAML 的 widget 覆盖优先于类型映射', () => {
    expect(resolveWidget(field('one_line', { widget: { child: 'voice_first' } }), 'child')).toBe(
      'default',
    )
    expect(resolveWidget(field('mood', { widget: { child: 'coin_pad' } }), 'child')).toBe('stepper')
    expect(resolveWidget(field('note', { widget: { child: 'hidden' } }), 'child')).toBe('hidden')
    // 覆盖只在对应年龄档生效
    expect(resolveWidget(field('mood', { widget: { child: 'emoji_faces' } }), 'adult')).toBe(
      'default',
    )
  })
})

describe('stepperStep', () => {
  it('默认步长 1，字段声明的 step 优先', () => {
    expect(stepperStep(field('n', { type: 'number' }))).toBe(1)
    expect(stepperStep(field('n', { type: 'number', step: 5 }))).toBe(5)
  })
})

describe('pruneValues', () => {
  it('丢掉空值并去掉两端空白', () => {
    expect(
      pruneValues({
        amount: 12.5,
        title: '  记账  ',
        note: '',
        mood: null,
        tags: [],
        kept: ['a'],
      }),
    ).toEqual({ amount: 12.5, title: '记账', kept: ['a'] })
  })
})

describe('numberBounds', () => {
  it('量表固定在 1..5，金额步长 0.01', () => {
    expect(numberBounds(field('mood', { type: 'scale5' }))).toEqual({
      min: 1,
      max: 5,
      step: 1,
    })
    expect(numberBounds(field('amount', { type: 'money' })).step).toBe(0.01)
  })
})

describe('resolveOptions（共用 schemas/dictionaries）', () => {
  it('dict: 引用能解析成带中文标签的选项', () => {
    const options = resolveOptions('dict:expense_category')
    expect(options.length).toBeGreaterThan(0)
    expect(options.map((o) => o.label)).toContain('餐饮')
  })

  it('自由数组按「值即标签」处理，未知字典返回空', () => {
    expect(resolveOptions(['CNY', 'JPY'])).toEqual([
      { key: 'CNY', label: 'CNY' },
      { key: 'JPY', label: 'JPY' },
    ])
    expect(resolveOptions('dict:not_here')).toEqual([])
  })
})
