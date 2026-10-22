/** 会话令牌的存取（第 3.4 节 / 第 7.3 节）。
 *
 * 令牌由启动器通过启动参数交给前端，这里只做三件事：
 * 1. 从 URL fragment 读（`#t=<令牌>`，fragment 不进服务端日志与 Referer）；
 * 2. 存进 sessionStorage（关掉标签页即失效，不落盘）；
 * 3. 提供手动粘贴入口，方便开发期直连核心进程。
 */

const STORAGE_KEY = 'lifeos.token'
const HASH_PREFIX = 't='

let token: string | null = null

export function bootstrapToken(): string | null {
  const hash = window.location.hash.replace(/^#/, '')
  if (hash.startsWith(HASH_PREFIX)) {
    const value = hash.slice(HASH_PREFIX.length).trim()
    if (value) {
      sessionStorage.setItem(STORAGE_KEY, value)
      // 抹掉地址栏里的令牌，避免被截图/历史记录留下
      window.history.replaceState(null, '', window.location.pathname + window.location.search)
    }
  }
  token = sessionStorage.getItem(STORAGE_KEY)
  return token
}

export function getToken(): string | null {
  return token ?? sessionStorage.getItem(STORAGE_KEY)
}

export function setToken(value: string): void {
  const trimmed = value.trim()
  token = trimmed || null
  if (token) {
    sessionStorage.setItem(STORAGE_KEY, token)
  } else {
    sessionStorage.removeItem(STORAGE_KEY)
  }
}
