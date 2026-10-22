/** 力导向布局（第 11.1 节「关系」页、P4）。
 *
 * 不引入 d3/cytoscape：CSP `default-src 'self'` 禁外部资源，且离线优先，
 * 这里用确定性的 Fruchterman–Reingold 迭代（固定初始位置、固定迭代次数），
 * 同输入必得同输出，方便单测与可复现截图。
 */

export interface LayoutEdge {
  src: string
  dst: string
  weight?: number
}

export interface Point {
  x: number
  y: number
}

export interface LayoutResult {
  positions: Record<string, Point>
  width: number
  height: number
  padding: number
}

const PADDING = 48

/** 把节点铺在圆周上：确定性、无随机。 */
function seed(nodeCount: number, cx: number, cy: number, radius: number): Point[] {
  if (nodeCount === 1) return [{ x: cx, y: cy }]
  const out: Point[] = []
  for (let i = 0; i < nodeCount; i += 1) {
    const angle = (2 * Math.PI * i) / nodeCount - Math.PI / 2
    out.push({ x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) })
  }
  return out
}

export function layoutGraph(
  nodeIds: string[],
  edges: LayoutEdge[],
  width: number,
  height: number,
  iterations = 300,
): LayoutResult {
  const n = nodeIds.length
  const cx = width / 2
  const cy = height / 2
  const inner = Math.min(width, height) / 2 - PADDING
  const points = seed(n, cx, cy, Math.max(inner, 40))
  if (n <= 1) {
    return {
      positions: nodeIds.length ? { [nodeIds[0]]: points[0] } : {},
      width,
      height,
      padding: PADDING,
    }
  }

  const index = new Map(nodeIds.map((id, i) => [id, i]))
  const links = edges
    .filter((e) => index.has(e.src) && index.has(e.dst) && e.src !== e.dst)
    .map((e) => ({
      a: index.get(e.src) as number,
      b: index.get(e.dst) as number,
      w: e.weight ?? 1,
    }))

  const area = (inner * 2) * (inner * 2)
  const k = Math.sqrt(area / n)
  let temperature = inner / 6

  for (let step = 0; step < iterations; step += 1) {
    const dispX = new Array<number>(n).fill(0)
    const dispY = new Array<number>(n).fill(0)

    // 斥力：所有节点两两排斥
    for (let i = 0; i < n; i += 1) {
      for (let j = i + 1; j < n; j += 1) {
        let dx = points[i].x - points[j].x
        let dy = points[i].y - points[j].y
        const dist = Math.hypot(dx, dy) || 0.01
        const force = (k * k) / dist
        dx /= dist
        dy /= dist
        dispX[i] += dx * force
        dispY[i] += dy * force
        dispX[j] -= dx * force
        dispY[j] -= dy * force
      }
    }

    // 引力：有边的节点互相吸引，权重越大越紧
    for (const link of links) {
      let dx = points[link.a].x - points[link.b].x
      let dy = points[link.a].y - points[link.b].y
      const dist = Math.hypot(dx, dy) || 0.01
      const force = ((dist * dist) / k) * (1 + Math.log(link.w))
      dx /= dist
      dy /= dist
      dispX[link.a] -= dx * force
      dispY[link.a] -= dy * force
      dispX[link.b] += dx * force
      dispY[link.b] += dy * force
    }

    // 位移 + 降温 + 边界收敛
    for (let i = 0; i < n; i += 1) {
      const dist = Math.hypot(dispX[i], dispY[i]) || 0.01
      const limit = Math.min(dist, temperature)
      points[i].x += (dispX[i] / dist) * limit
      points[i].y += (dispY[i] / dist) * limit
      points[i].x = Math.min(width - PADDING, Math.max(PADDING, points[i].x))
      points[i].y = Math.min(height - PADDING, Math.max(PADDING, points[i].y))
    }
    temperature = Math.max(temperature * 0.97, 0.1)
  }

  const positions: Record<string, Point> = {}
  nodeIds.forEach((id, i) => {
    positions[id] = { x: Math.round(points[i].x), y: Math.round(points[i].y) }
  })
  return { positions, width, height, padding: PADDING }
}
