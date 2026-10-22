import { describe, expect, it } from 'vitest'

import { layoutGraph } from './forceLayout'

describe('layoutGraph', () => {
  it('同输入同输出（确定性）', () => {
    const ids = ['a', 'b', 'c', 'd']
    const edges = [
      { src: 'a', dst: 'b' },
      { src: 'b', dst: 'c', weight: 3 },
    ]
    const first = layoutGraph(ids, edges, 400, 300, 120)
    const second = layoutGraph(ids, edges, 400, 300, 120)
    expect(first.positions).toEqual(second.positions)
  })

  it('所有坐标有限且落在画布内', () => {
    const ids = ['a', 'b', 'c']
    const { positions, padding } = layoutGraph(ids, [{ src: 'a', dst: 'b' }], 400, 300, 120)
    for (const id of ids) {
      const p = positions[id]
      expect(Number.isFinite(p.x)).toBe(true)
      expect(Number.isFinite(p.y)).toBe(true)
      expect(p.x).toBeGreaterThanOrEqual(padding)
      expect(p.x).toBeLessThanOrEqual(400 - padding)
      expect(p.y).toBeGreaterThanOrEqual(padding)
      expect(p.y).toBeLessThanOrEqual(300 - padding)
    }
  })

  it('空图与单点不崩', () => {
    expect(layoutGraph([], [], 400, 300).positions).toEqual({})
    const one = layoutGraph(['only'], [], 400, 300)
    expect(one.positions.only).toEqual({ x: 200, y: 150 })
  })
})
