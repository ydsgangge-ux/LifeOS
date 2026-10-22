/** 长期对象选择器的数据源（第 5 章）：加载与就地新建。
 *
 * 放在 lib 里而不是组件文件里，避免「组件文件同时导出函数」的 fast-refresh 限制。
 */

import { api } from './api'

export type RefFieldType = 'person' | 'hobby' | 'skill' | 'goal' | 'plan' | 'resource'

export interface RefOption {
  id: string
  label: string
}

const LOADERS: Record<RefFieldType, () => Promise<RefOption[]>> = {
  person: async () =>
    (await api.listPeople()).items.map((item) => ({ id: item.id, label: item.display_name })),
  hobby: async () =>
    (await api.listHobbies()).items.map((item) => ({ id: item.id, label: item.name })),
  skill: async () =>
    (await api.listSkills()).items.map((item) => ({ id: item.id, label: item.name })),
  goal: async () =>
    (await api.listGoals()).items.map((item) => ({ id: item.id, label: item.title })),
  plan: async () =>
    (await api.listPlans()).items.map((item) => ({ id: item.id, label: item.title })),
  resource: async () =>
    (await api.listResources()).items.map((item) => ({ id: item.id, label: item.name })),
}

const CREATORS: Record<RefFieldType, (name: string) => Promise<RefOption>> = {
  person: async (name) => {
    const created = await api.createPerson(name)
    return { id: created.id, label: created.display_name }
  },
  hobby: async (name) => {
    const created = await api.createHobby(name)
    return { id: created.id, label: created.name }
  },
  skill: async (name) => {
    const created = await api.createSkill(name)
    return { id: created.id, label: created.name }
  },
  goal: async (name) => {
    const created = await api.createGoal({ title: name })
    return { id: created.id, label: created.title }
  },
  plan: async (name) => {
    const created = await api.createPlan({ title: name })
    return { id: created.id, label: created.title }
  },
  resource: async (name) => {
    const created = await api.createResource({ name })
    return { id: created.id, label: created.name }
  },
}

/** 同一份选择列表在多个字段/多次切表单之间复用，避免重复请求。 */
const cache = new Map<RefFieldType, RefOption[]>()

export function cachedRefOptions(fieldType: RefFieldType): RefOption[] | null {
  return cache.get(fieldType) ?? null
}

export async function loadRefOptions(fieldType: RefFieldType): Promise<RefOption[]> {
  const cached = cache.get(fieldType)
  if (cached) return cached
  const items = await LOADERS[fieldType]()
  cache.set(fieldType, items)
  return items
}

export async function createRefOption(
  fieldType: RefFieldType,
  name: string,
): Promise<RefOption> {
  const created = await CREATORS[fieldType](name)
  const next = [...(cache.get(fieldType) ?? []), created]
  cache.set(fieldType, next)
  return created
}

/** 台账里新建资源后调用，下一次打开表单会重新拉取列表。 */
export function invalidateRefCache(fieldType: RefFieldType): void {
  cache.delete(fieldType)
}
