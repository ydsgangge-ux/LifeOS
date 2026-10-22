/** Playwright 端到端配置（P6 W6.3）。
 *
 * 只跑 chromium（headless）：e2e 的流程是有状态的（建档 → 解锁 → 录入 → 锁定 → 再解锁，
 * 以及「切语言 → 刷新」），所以 workers=1、fullyParallel=false，用例按声明顺序串行执行。
 *
 * baseURL 不写死：核心监听 127.0.0.1 的**随机/固定**端口，且会话令牌是每次启动随机生成的，
 * 只有 e2e/run.mjs 拉起核心并解析 LIFEOS_READY 握手行之后才知道地址，由它通过
 * LIFEOS_E2E_URL 注入。直接用 `npx playwright test` 跑会因为缺这个环境变量而报错。
 */

import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.LIFEOS_E2E_URL
if (!baseURL) {
  throw new Error(
    '缺少 LIFEOS_E2E_URL：请用 `npm run e2e`（e2e/run.mjs）启动——由它拉起核心进程并注入地址与令牌。',
  )
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL,
    headless: true,
    trace: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
