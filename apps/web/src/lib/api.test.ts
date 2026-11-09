import { afterEach, describe, expect, it, vi } from 'vitest'

import { newIdempotencyKey } from './api'

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('newIdempotencyKey', () => {
  it('安全上下文下直接用 crypto.randomUUID', () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'fixed-uuid' })
    expect(newIdempotencyKey()).toBe('fixed-uuid')
  })

  it('randomUUID 不可用时（手机局域网 http）退回 getRandomValues', () => {
    // 局域网 http://IP:port 不是安全上下文，浏览器不提供 randomUUID
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => {
        bytes.fill(1)
        return bytes
      },
    })
    const key = newIdempotencyKey()
    expect(key).toMatch(UUID_V4)
    expect(key).toBe('01010101-0101-4101-8101-010101010101')
  })

  it('连 getRandomValues 都没有时仍能生成合法 UUID', () => {
    vi.stubGlobal('crypto', undefined)
    expect(newIdempotencyKey()).toMatch(UUID_V4)
  })
})
