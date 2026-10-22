"""便携启动器（P6 W2/W3）：双击图标 → 起核心 → 打开系统浏览器 → 界面内退出。

设计要点

- **单实例**：用数据目录里的 ``launcher.lock`` 文件锁。第二份启动器拿不到锁时
  不新起核心，直接打开已有实例的地址（验收 A3：重复双击不产生第二个核心）。
- **握手**：核心 stdout 的第一行固定为 ``LIFEOS_READY {json}``，据此拿到地址，
  不去解析人类可读提示（W2.2）。核心的 stderr 落到数据目录的日志文件里，
  不占用户视线、也不会因为窗口化形态没有控制台而丢消息。
- **退出带走核心**：核心是子进程，启动器退出时先请求优雅停服、再兜底强杀，
  不留孤儿（W3.3）。界面内的「退出 LifeOS」经 POST /system/shutdown 触发。
- **不联网**：本模块只做进程 / 文件操作与 ``webbrowser``，不含任何网络库
  （契约管不到启动器，靠 6.4 的显式检查守卫，验收 A2）。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import IO, Any

from .api import READY_PREFIX
from .config import AppConfig, load_config
from .i18n import DEFAULT_LOCALE, available_locales, t

CORE_FLAG = "--core"
SMOKE_FLAG = "--smoke-sqlcipher"
LOCK_NAME = "launcher.lock"
CORE_LOG_NAME = "launcher-core.log"

READY_TIMEOUT_SECONDS = 60.0
SHUTDOWN_GRACE_SECONDS = 8.0

# 退出码：0 正常；非 0 表示启动器自己拦下的失败（不是核心的退出码）
EXIT_OK = 0
EXIT_BAD_DATA_DIR = 2
EXIT_CORE_FAILED = 3
EXIT_MISSING_RESOURCES = 4


# --------------------------------------------------------------------------
# 单实例
# --------------------------------------------------------------------------
def _lock_file(fh: IO[str]) -> None:
    """对已打开的文件加排他非阻塞锁；拿不到就抛 OSError。

    进程崩溃时操作系统会自动释放，所以不需要额外处理「陈旧锁」。
    """
    if os.name == "nt":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        # 这个分支只在 POSIX 上执行；Windows 的 mypy 环境里没有 fcntl 的完整声明
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]


class InstanceLock:
    """数据目录里的启动器锁（W2.4）。

    两个文件各管一件事：

    - ``launcher.lock``：只用来互相排斥，**内容无意义、也永远不去读**。
      原因是 Windows 的字节区间锁会连**读**一起挡掉，把地址写在被锁的文件里，
      第二个启动器就读不出来了（踩过）。
    - ``launcher.json``：写完就关的一行 JSON ``{"pid", "url"}``。锁被别的实例
      持有时，后来的启动器读它拿到已有地址，直接打开、不再起第二个核心。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.state_path = path.with_suffix(".json")
        self._fh: IO[str] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+", encoding="utf-8")
        try:
            # msvcrt.locking 锁的是「从当前位置起的 1 个字节」，空文件没有那个
            # 位置，所以先补一个字节再锁。
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                fh.write("\n")
                fh.flush()
            _lock_file(fh)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def read_url(self) -> str | None:
        try:
            raw = self.state_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not raw:
            return None
        try:
            data: Any = json.loads(raw)
        except ValueError:
            return None
        url = data.get("url") if isinstance(data, dict) else None
        return url if isinstance(url, str) and url else None

    def write_url(self, url: str) -> None:
        """原子替换状态文件，避免读到写一半的内容。"""
        payload = json.dumps({"pid": os.getpid(), "url": url})
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.state_path)

    def release(self) -> None:
        if self._fh is not None:
            self._fh.close()  # 关闭即释放锁
            self._fh = None


# --------------------------------------------------------------------------
# 输出与错误面（W3.4：可本地化、不露堆栈）
# --------------------------------------------------------------------------
def _say(text: str) -> None:
    """有终端才打印：窗口化打包形态下 stdout 可能是 None。"""
    if sys.stdout is None:
        return
    with suppress(OSError, ValueError):  # pragma: no cover - 终端已断开
        print(text, flush=True)


def _show_error(message: str, title: str) -> None:
    """把可本地化的失败提示告诉用户（P6 W3.4）：窗口化形态没有控制台，
    Windows 用系统对话框，其它平台退回 stdout。绝不暴露堆栈。"""
    _say(message)
    if os.name != "nt":
        return
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is not None:
            windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
    except Exception:  # pragma: no cover - 对话框不可用时只剩 stdout
        pass


def _detect_locale(locales: Sequence[str] | None = None) -> str:
    """启动器文案用哪种语言：环境变量 > 系统语言 > 默认。

    浏览器里的选择存在 localStorage，启动器读不到；这里只做尽力而为，
    真正的界面语言仍由前端决定。``locales`` 只在单测里注入，便于验证
    多语言下的挑选逻辑而不必先摆好四份词表。
    """
    available = list(locales) if locales is not None else available_locales()
    override = os.environ.get("LIFEOS_LOCALE", "").strip()
    if override in available:
        return override
    try:
        import locale as _locale

        tag = (_locale.getlocale()[0] or "").replace("_", "-")
    except Exception:  # pragma: no cover - 取不到就用默认
        tag = ""
    if tag.startswith("zh"):
        return "zh-CN" if "zh-CN" in available else DEFAULT_LOCALE
    for candidate in available:
        if tag and tag.split("-")[0] == candidate.split("-")[0]:
            return candidate
    return DEFAULT_LOCALE


# --------------------------------------------------------------------------
# 核心子进程
# --------------------------------------------------------------------------
def core_argv() -> list[str]:
    """启动核心用的命令行。

    - 源码运行（开发 / 单测）：``python -m lifeos.api``
    - 冻结运行时（PyInstaller）：自身加 ``--core``，由 main() 转交给 api.main
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, CORE_FLAG, "--no-print-url"]
    return [sys.executable, "-m", "lifeos.api", "--no-print-url"]


def _core_env() -> dict[str, str]:
    env = dict(os.environ)
    if not getattr(sys, "frozen", False):
        # 源码运行时让子进程一定找得到 lifeos 包，不受当前工作目录影响
        root = str(Path(__file__).resolve().parents[1])
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{root}{os.pathsep}{existing}" if existing else root
    return env


def _read_ready(proc: subprocess.Popen[str], timeout: float) -> dict[str, Any] | None:
    """读核心 stdout 里的握手行；核心提前退出或超时返回 None。

    读线程是守护线程：即使核心一直不吐握手行，启动器也不会卡死在这里。
    """
    lines: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def _pump() -> None:
        stdout = proc.stdout
        if stdout is None:  # pragma: no cover - 我们总是用 PIPE
            lines.put(None)
            return
        for line in stdout:
            if not line.startswith(READY_PREFIX):
                continue
            try:
                data: Any = json.loads(line[len(READY_PREFIX) :])
            except ValueError:
                continue
            if isinstance(data, dict):
                lines.put(data)
                return
        lines.put(None)  # 流结束（核心退出）

    threading.Thread(target=_pump, daemon=True, name="lifeos-handshake").start()
    try:
        return lines.get(timeout=timeout)
    except queue.Empty:
        return None


def _terminate(proc: subprocess.Popen[str]) -> None:
    """兜底收尾：核心没自己退就请求优雅退出，再超时强杀（W3.3）。"""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=SHUTDOWN_GRACE_SECONDS)
    except subprocess.TimeoutExpired:  # pragma: no cover - 罕见：核心卡住不响应
        proc.kill()
        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)


def _drain_stream(stream: IO[str] | None, sink: IO[str]) -> None:
    """握手后继续读走核心 stdout。

    核心的 stdout 是管道，启动器只读到握手行就不再读；之后 uvicorn 每处理一个
    请求都会往 stdout 写日志，管道缓冲区写满后核心会卡在 flush 上（整个事件
    循环冻结，所有请求超时）。这里用后台线程持续排空，写进同一个日志文件。
    """
    if stream is None:  # pragma: no cover - 我们总是用 PIPE
        return
    with suppress(Exception):
        for line in stream:
            sink.write(line)
            sink.flush()


def launch(cfg: AppConfig, locale: str, data_dir: Path, lock: InstanceLock) -> int:
    """起核心 → 打开浏览器 → 等它退出（界面内「退出 LifeOS」）。"""
    missing = [p for p in (cfg.schemas_dir, cfg.i18n_dir) if not p.is_dir()]
    if missing:
        _show_error(
            t("desktop.error.resources", locale, paths=", ".join(str(p) for p in missing)),
            t("desktop.error.title", locale),
        )
        return EXIT_MISSING_RESOURCES

    log_path = data_dir / CORE_LOG_NAME
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(
            core_argv(),
            stdout=subprocess.PIPE,
            stderr=log,
            env=_core_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            ready = _read_ready(proc, READY_TIMEOUT_SECONDS)
            if ready is None:
                _show_error(
                    t("desktop.error.core_failed", locale, log=str(log_path)),
                    t("desktop.error.title", locale),
                )
                return EXIT_CORE_FAILED
            url = str(ready.get("url") or "")
            # 握手行已经读走，后面的 stdout 交给后台线程排空，否则管道写满会冻住核心
            threading.Thread(
                target=_drain_stream, args=(proc.stdout, log), daemon=True
            ).start()
            # 把地址写进锁文件：下次双击时，新启动器读它就能直接打开已有实例
            lock.write_url(url)
            _say(t("desktop.launched", locale, url=url))
            webbrowser.open(url)
            proc.wait()  # 用户在界面里点「退出 LifeOS」→ 核心退出 → 启动器收工
            return EXIT_OK
        except KeyboardInterrupt:  # pragma: no cover - 终端里 Ctrl+C
            return EXIT_OK
        finally:
            _terminate(proc)


def _smoke() -> int:
    """``--smoke-sqlcipher``：打包冒烟用（W1.5），不依赖 pytest。"""
    from .diagnostics import sqlcipher_smoke

    try:
        info = sqlcipher_smoke()
    except Exception as exc:
        _say(f"SQLCipher smoke FAILED: {exc}")
        return 1
    _say(f"SQLCipher smoke OK cipher={info['cipher_version']} python={info['python']}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if CORE_FLAG in args:
        from .api import main as api_main

        return api_main([a for a in args if a != CORE_FLAG])
    if SMOKE_FLAG in args:
        return _smoke()
    return _run_launcher()


def _run_launcher() -> int:
    locale = _detect_locale()
    cfg = load_config()
    data_dir = cfg.data_dir
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _show_error(
            t("desktop.error.data_dir", locale, detail=str(exc)),
            t("desktop.error.title", locale),
        )
        return EXIT_BAD_DATA_DIR

    lock = InstanceLock(data_dir / LOCK_NAME)
    if not lock.acquire():
        url = lock.read_url()
        if url:
            # 已有实例：只打开它的地址，不再起第二个核心（验收 A3）
            webbrowser.open(url)
            return EXIT_OK
        _show_error(
            t("desktop.error.already_starting", locale),
            t("desktop.error.title", locale),
        )
        return EXIT_OK
    try:
        return launch(cfg, locale, data_dir, lock)
    finally:
        lock.release()


__all__ = ["InstanceLock", "core_argv", "launch", "main"]


if __name__ == "__main__":
    # 双击入口（LifeOS.cmd）走的就是 `python -m lifeos.launcher` 这条命令行；
    # 少了这个守卫，解释器只会 import 一遍模块就静默退出（退出码 0，什么都没发生），
    # 而且单测直接调 main() 是发现不了的。
    raise SystemExit(main())
