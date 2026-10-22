/** e2e 启动器（P6 W6.4）：拉起核心 → 解析握手行 → 跑 Playwright → 收尾清理。
 *
 * 为什么要这一步、而不是让 Playwright 直接起 webServer：
 *   - 核心只监听 127.0.0.1，并带 LocalOnlyMiddleware（非回环 Host / Origin 一律
 *     400/403），所以浏览器必须访问 http://127.0.0.1:<port>/，不能用别名。
 *   - 会话令牌是启动时随机生成的，只在核心 stdout 上打印一行机器可读握手行
 *     `LIFEOS_READY {"port":..., "token":..., "url":"http://127.0.0.1:<port>/#t=<token>"}`。
 *     只有本脚本解析它才知道地址与令牌，再通过 LIFEOS_E2E_URL / LIFEOS_E2E_TOKEN
 *     注入给 Playwright（用例不从代码硬编码令牌）。
 *   - 核心必须跑在 cwd=services/core，命令是 `python -m lifeos.api --port <port>`。
 *   - 必须给核心设 LIFEOS_DATA_DIR=<临时目录>，否则会写到用户真实的 ~/.lifeos。
 *
 * Python 解释器（重要）：
 *   - 优先读环境变量 LIFEOS_PYTHON（CI 的 Linux runner 上设成 `python` 即可复用同一份脚本）。
 *   - 没有该变量时回退到本机开发 venv：<repo>/.venv/Scripts/python.exe。
 *   - **不要**用 release/LifeOS/runtime/python/python.exe 那个随包便携解释器：在 Windows
 *     这台机器上它被安全软件静默拦掉了 loopback 入站，会永久卡住且没有任何报错；
 *     只有开发 venv 的解释器能正常起服务（已验证）。
 */

import { spawn } from 'node:child_process'
import fs from 'node:fs'
import { createRequire } from 'node:module'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const webDir = path.resolve(here, '..')
const repoRoot = path.resolve(webDir, '..', '..')
const coreDir = path.join(repoRoot, 'services', 'core')

const python =
  process.env.LIFEOS_PYTHON || path.join(repoRoot, '.venv', 'Scripts', 'python.exe')

const READY_PREFIX = 'LIFEOS_READY '
// 首选固定端口（便于排障）；若被占用则回退到 0（由内核挑空闲端口）。
// 无论走哪个，最终都以握手行里的 port 为准。
const PORT_CANDIDATES = [8787, 0]
const HANDSHAKE_TIMEOUT_MS = 60_000
const SHUTDOWN_TIMEOUT_MS = 8_000

function rmDirQuiet(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true })
  } catch {
    // 文件可能仍被句柄占用，留给系统清理即可，不阻断退出码
  }
}

function waitForExit(child, ms) {
  return new Promise((resolve) => {
    if (child.exitCode !== null) {
      resolve(true)
      return
    }
    const timer = setTimeout(() => resolve(false), ms)
    child.once('exit', () => {
      clearTimeout(timer)
      resolve(true)
    })
  })
}

/** 起一个核心进程，逐行读 stdout 直到解析出 LIFEOS_READY 握手行。 */
function startCore(port, dataDir) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, ['-m', 'lifeos.api', '--port', String(port)], {
      cwd: coreDir,
      env: { ...process.env, LIFEOS_DATA_DIR: dataDir, PYTHONIOENCODING: 'utf-8' },
      stdio: ['ignore', 'pipe', 'pipe'],
    })

    let stdoutBuf = ''
    let stderrBuf = ''
    let settled = false

    const finish = (fn) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      fn()
    }

    const timer = setTimeout(() => {
      child.kill()
      finish(() =>
        reject(new Error(`等待握手行超时（${HANDSHAKE_TIMEOUT_MS}ms）。核心 stderr:\n${stderrBuf}`)),
      )
    }, HANDSHAKE_TIMEOUT_MS)

    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stderr.on('data', (chunk) => {
      stderrBuf += chunk
    })

    child.stdout.on('data', (chunk) => {
      stdoutBuf += chunk
      let idx
      while ((idx = stdoutBuf.indexOf('\n')) >= 0) {
        const line = stdoutBuf.slice(0, idx).trimEnd()
        stdoutBuf = stdoutBuf.slice(idx + 1)
        if (!line.startsWith(READY_PREFIX)) continue
        try {
          const info = JSON.parse(line.slice(READY_PREFIX.length))
          finish(() => resolve({ child, info }))
        } catch {
          // 握手行损坏：交给超时兜底
        }
      }
    })

    child.on('error', (err) => {
      finish(() => reject(err))
    })

    child.on('exit', (code, signal) => {
      finish(() =>
        reject(
          new Error(
            `核心进程提前退出（code=${code} signal=${signal}）。核心 stderr:\n${stderrBuf}`,
          ),
        ),
      )
    })
  })
}

async function startCoreWithFallback(dataDir) {
  let lastError
  for (const port of PORT_CANDIDATES) {
    try {
      return await startCore(port, dataDir)
    } catch (err) {
      lastError = err
      process.stderr.write(`[e2e] 用端口 ${port} 启动核心失败：${err.message}\n`)
    }
  }
  throw lastError
}

function playwrightCli() {
  const require = createRequire(import.meta.url)
  const pkgPath = require.resolve('@playwright/test/package.json')
  const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'))
  const bin = typeof pkg.bin === 'string' ? pkg.bin : pkg.bin.playwright
  return path.join(path.dirname(pkgPath), bin)
}

/** 跑 Playwright：地址与令牌经环境变量下发（config 的 baseURL 读 LIFEOS_E2E_URL）。 */
function runPlaywright(appUrl, token) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [playwrightCli(), 'test'], {
      cwd: webDir,
      env: { ...process.env, LIFEOS_E2E_URL: appUrl, LIFEOS_E2E_TOKEN: token },
      stdio: 'inherit',
    })
    child.on('exit', (code) => resolve(code ?? 1))
  })
}

/** 优雅停服：调 POST /system/shutdown；没停下来就直接杀，绝不留孤儿进程。 */
async function stopCore(child, appUrl, token) {
  if (!child || child.exitCode !== null) return
  try {
    await fetch(`${appUrl}/api/v1/system/shutdown`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    })
  } catch {
    // 核心可能已经停了
  }
  const exited = await waitForExit(child, SHUTDOWN_TIMEOUT_MS)
  if (!exited) {
    child.kill('SIGKILL')
    await waitForExit(child, 3_000)
  }
  if (child.exitCode === null) child.kill()
}

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'lifeos-e2e-'))
let started = null
let exitCode = 1
try {
  started = await startCoreWithFallback(dataDir)
  const appUrl = `http://127.0.0.1:${started.info.port}`
  process.stdout.write(`[e2e] 核心就绪：${appUrl}（数据目录 ${dataDir}）\n`)
  exitCode = await runPlaywright(appUrl, started.info.token)
} catch (err) {
  process.stderr.write(`[e2e] 启动失败：${err?.stack ?? err}\n`)
  exitCode = 1
} finally {
  if (started) {
    await stopCore(started.child, `http://127.0.0.1:${started.info.port}`, started.info.token)
  }
  rmDirQuiet(dataDir)
}

process.exit(exitCode)
