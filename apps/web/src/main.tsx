import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App'
import './i18n'
import './styles/theme.css'
import { bootstrapToken } from './lib/session'
import { applyTheme, loadStoredTheme } from './theme/apply'

// 启动器把令牌放在 URL fragment（#t=...）：fragment 不会发给服务端，
// 也不会进 Referer；读到后立刻从地址栏抹掉，转存到 sessionStorage。
bootstrapToken()

// 先落地主题再渲染，避免首屏闪一下默认配色（第 11.3 节）。
applyTheme(loadStoredTheme())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
