/** 端到端主链（P6 W6.3）：覆盖「一键启动 + 多语言」里程碑的两条验收主链。
 *
 * 约定：
 *   - 浏览器只访问 http://127.0.0.1:<port>（核心的 LocalOnlyMiddleware 会拒掉非回环
 *     Host / Origin，testserver 之类的别名会被 400/403 掉），baseURL 由 run.mjs 注入。
 *   - 会话令牌是随机的、只在握手行里出现，所以不从代码硬编码：run.mjs 通过
 *     LIFEOS_E2E_TOKEN 注入，用例把它补进地址栏 fragment（`#t=…`），前端读到后会
 *     立刻抹掉地址栏里的令牌、转存 sessionStorage。
 *   - 两条用例有先后依赖（先建档，后解锁复用同一档案），靠 workers=1 + 单文件声明顺序
 *     串行执行来保证。
 */

import { expect, test, type Page } from '@playwright/test'

const TOKEN = process.env.LIFEOS_E2E_TOKEN ?? ''
const PASSPHRASE = 'e2e-passphrase-2026'
const ALIAS = 'E2E 档案'
const ENTRY_TITLE = 'e2e 今日打卡'

async function openWithToken(page: Page): Promise<void> {
  // baseURL = http://127.0.0.1:<port>（run.mjs 注入）；这里只把 fragment 里的令牌补上。
  await page.goto(`/#t=${encodeURIComponent(TOKEN)}`)
}

test('建档 → 解锁 → 录入今日打卡 → 锁定 → 再解锁', async ({ page }) => {
  await openWithToken(page)

  // 1) 建档：空档案列表 + 「新建档案」入口，填别名 / 年龄档 / 口令（含再输一次）
  await expect(page.getByText('还没有档案。第一个档案可以自己建。')).toBeVisible()
  await page.getByRole('button', { name: '新建档案' }).click()
  await page.getByTestId('new-alias').fill(ALIAS)
  await page.getByTestId('new-age-band').selectOption('adult')
  await page.getByTestId('new-passphrase').fill(PASSPHRASE)
  await page.getByTestId('new-repeat').fill(PASSPHRASE)
  await page.getByRole('button', { name: '创建' }).click()

  // 2) 一次性恢复密钥：只显示这一次，确认真的给出来了再记下来
  await expect(page.getByRole('heading', { name: `档案已创建：${ALIAS}` })).toBeVisible()
  const recoveryKey = (await page.getByTestId('recovery-key').innerText()).trim()
  expect(recoveryKey.length).toBeGreaterThan(0)
  await page.getByRole('button', { name: '我已保存，返回' }).click()

  // 3) 用口令解锁
  await page.getByRole('button', { name: '解锁' }).click()
  await page.getByTestId('unlock-credential').fill(PASSPHRASE)
  await page.getByRole('button', { name: '解锁' }).click()
  await expect(page.getByRole('button', { name: '锁定' })).toBeVisible()

  // 4) 录入一条今日打卡（daily 表单由 YAML 定义、FormRenderer 渲染）
  await expect(page.getByRole('heading', { name: '今日打卡' })).toBeVisible()
  await page.getByTestId('field-mood').getByRole('button', { name: '4' }).click()
  await page.getByTestId('field-title').getByRole('textbox').fill(ENTRY_TITLE)
  await page.getByRole('button', { name: '保存' }).click()
  await expect(page.getByText('已保存。', { exact: true })).toBeVisible()
  await expect(page.getByText(ENTRY_TITLE, { exact: true })).toBeVisible()

  // 5) 锁定：密钥从内存丢弃，回到解锁页
  await page.getByRole('button', { name: '锁定' }).click()
  await expect(page.getByText('已锁定，加密密钥已从内存中丢弃。', { exact: true })).toBeVisible()

  // 6) 再用口令解锁成功
  await page.getByRole('button', { name: '解锁' }).click()
  await page.getByTestId('unlock-credential').fill(PASSPHRASE)
  await page.getByRole('button', { name: '解锁' }).click()
  await expect(page.getByRole('button', { name: '锁定' })).toBeVisible()
})

test('切换语言后刷新仍保持（A5）', async ({ page }) => {
  await openWithToken(page)

  // 复用上一条用例建好的档案解锁
  await page.getByRole('button', { name: '解锁' }).click()
  await page.getByTestId('unlock-credential').fill(PASSPHRASE)
  await page.getByRole('button', { name: '解锁' }).click()

  // 顶部语言选择（aria-label = locale.title，默认 zh-CN 为「界面语言」）
  const picker = page.getByLabel('界面语言')
  await expect(picker).toBeVisible()
  await picker.selectOption('en')
  // 界面立刻切到英文（i18next 先生效，档案后同步）
  await expect(page.getByRole('button', { name: 'Record', exact: true })).toBeVisible()

  // 刷新：令牌仍在 sessionStorage，界面语言来自 localStorage
  await page.reload()
  await expect(
    page.getByText('Pick a profile to unlock; your data stays on this machine throughout.'),
  ).toBeVisible()
})
