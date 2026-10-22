"""P6 W2/W3：启动器的握手行、单实例、退出带走核心、--no-print-url。

这些都不真的阻塞在 uvicorn 上：要么让 uvicorn 只跑完 startup 就收工，
要么用一个「打完握手就退出」的子进程，避免单测挂住。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import uvicorn

from lifeos import launcher
from lifeos.api import READY_PREFIX
from lifeos.api import main as api_main
from lifeos.config import load_config

CORE_ROOT = str(Path(launcher.__file__).resolve().parents[1])

# LocalOnlyMiddleware 只放行回环地址的主机名，TestClient 默认的 testserver 会被
# 判成 400 bad_host（与 test_api 等既有测试一致，这里必须显式给定）。
BASE_URL = "http://127.0.0.1:8000"


@pytest.fixture
def no_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 uvicorn 只走一遍 startup（真实 bind 一个内核分配的端口）就收工。

    握手行是在**监听完成之后**才打印的（W2.2），所以不能简单地把 run 换成
    空函数，否则握手行根本不会出现。
    """

    def _run(self: uvicorn.Server) -> None:
        async def _start_only() -> None:
            config = self.config
            if not config.loaded:
                config.load()
            self.lifespan = config.lifespan_class(config)
            await self.startup()

        asyncio.run(_start_only())

    monkeypatch.setattr(uvicorn.Server, "run", _run)


# --------------------------------------------------------------------------
# 握手行与 --no-print-url（W2.2 / W2.3）
# --------------------------------------------------------------------------
def _ready_line(out: str) -> dict:
    for line in out.splitlines():
        if line.startswith(READY_PREFIX):
            return json.loads(line[len(READY_PREFIX) :])
    raise AssertionError(f"没找到握手行：{out!r}")


def test_handshake_line_has_port_token_url(
    no_server: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LIFEOS_DATA_DIR", str(tmp_path))
    assert api_main([]) == 0

    data = _ready_line(capsys.readouterr().out)
    assert isinstance(data["port"], int) and data["port"] > 0
    assert isinstance(data["token"], str) and data["token"]
    assert data["url"].endswith(f"#t={data['token']}")


def test_no_print_url_keeps_handshake_but_drops_human_lines(
    no_server: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LIFEOS_DATA_DIR", str(tmp_path))
    assert api_main(["--no-print-url"]) == 0

    out = capsys.readouterr().out
    assert "LifeOS 核心已启动" not in out
    assert _ready_line(out)["port"] > 0


# --------------------------------------------------------------------------
# 单实例（W2.4）
# --------------------------------------------------------------------------
_SINGLETON_CHILD = """
import sys, time
from pathlib import Path
from lifeos.launcher import InstanceLock

lock = InstanceLock(Path(sys.argv[1]))
assert lock.acquire(), "child failed to take the lock"
lock.write_url(sys.argv[2])
print("LOCKED", flush=True)
time.sleep(float(sys.argv[3]))
"""


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{CORE_ROOT}{os.pathsep}{existing}" if existing else CORE_ROOT
    return env


def test_single_instance_blocks_second_launcher(tmp_path: Path) -> None:
    """已有实例时第二次启动必须拿不到锁，并能读到已有地址。"""
    lock_path = tmp_path / "launcher.lock"
    url = "http://127.0.0.1:4321/#t=abc"
    child = subprocess.Popen(
        [sys.executable, "-c", _SINGLETON_CHILD, str(lock_path), url, "30"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_child_env(),
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "LOCKED"

        second = launcher.InstanceLock(lock_path)
        assert second.acquire() is False
        assert second.read_url() == url

        child.kill()
        child.wait(timeout=10)

        # 持有者进程消失后锁自动释放：下一次启动恢复正常
        third = launcher.InstanceLock(lock_path)
        for _ in range(100):
            if third.acquire():
                break
            time.sleep(0.1)
        else:  # pragma: no cover - 锁没释放说明实现有 bug
            pytest.fail("持有者退出后锁仍未释放")
        third.release()
    finally:
        if child.poll() is None:  # pragma: no cover - 正常路径已在上面 kill
            child.kill()
            child.wait(timeout=10)


def test_state_file_survives_stale_content(tmp_path: Path) -> None:
    """状态文件是垃圾内容时不该崩，只当读不到地址。"""
    lock_path = tmp_path / "launcher.lock"
    lock = launcher.InstanceLock(lock_path)
    lock.state_path.write_text("not json at all", encoding="utf-8")
    assert lock.acquire() is True
    assert lock.read_url() is None
    lock.release()


# --------------------------------------------------------------------------
# 核心随启动器退出而退出（W3.3）
# --------------------------------------------------------------------------
def test_core_argv_frozen_uses_core_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    argv = launcher.core_argv()
    assert launcher.CORE_FLAG in argv
    assert "--no-print-url" in argv


def test_core_argv_dev_runs_api_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert launcher.core_argv()[1:] == ["-m", "lifeos.api", "--no-print-url"]


def _fake_core(script: str):
    return lambda: [sys.executable, "-c", script]


def test_launch_returns_when_core_exits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """核心一起一落，启动器随之收工，并留下可复用地址。"""
    ready = json.dumps({"port": 1, "token": "t", "url": "http://127.0.0.1:1/#t=t"})
    script = f"print({READY_PREFIX!r} + {ready!r}, flush=True)"
    monkeypatch.setattr(launcher, "core_argv", _fake_core(script))
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(launcher, "_show_error", lambda msg, title: None)

    lock = launcher.InstanceLock(tmp_path / "launcher.lock")
    assert lock.acquire() is True
    try:
        assert launcher.launch(load_config(), "zh-CN", tmp_path, lock) == launcher.EXIT_OK
        assert lock.read_url() == "http://127.0.0.1:1/#t=t"
    finally:
        lock.release()


def test_launch_reports_localized_error_without_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """核心起不来时给可本地化提示，不露堆栈（验收 A3 / S6）。"""
    shown: list[str] = []
    monkeypatch.setattr(launcher, "core_argv", _fake_core("raise SystemExit(7)"))
    monkeypatch.setattr(launcher, "_show_error", lambda msg, title: shown.append(msg))

    lock = launcher.InstanceLock(tmp_path / "launcher.lock")
    assert lock.acquire() is True
    try:
        code = launcher.launch(load_config(), "zh-CN", tmp_path, lock)
        assert code == launcher.EXIT_CORE_FAILED
    finally:
        lock.release()

    assert shown, "失败时必须给出提示"
    assert "Traceback" not in shown[0]
    assert "desktop.error.core_failed" not in shown[0]  # 渲染过，不是裸 key


def test_launch_refuses_when_resources_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """资源目录缺失时直接给可本地化提示，而不是等核心报一堆栈。"""
    shown: list[str] = []
    monkeypatch.setattr(launcher, "_show_error", lambda msg, title: shown.append(msg))
    cfg = load_config()
    cfg.schemas_dir = tmp_path / "nope-schemas"
    cfg.config_dir = tmp_path / "nope-config"

    lock = launcher.InstanceLock(tmp_path / "launcher.lock")
    assert lock.acquire() is True
    try:
        code = launcher.launch(cfg, "zh-CN", tmp_path, lock)
        assert code == launcher.EXIT_MISSING_RESOURCES
    finally:
        lock.release()
    assert shown and "Traceback" not in shown[0]


# --------------------------------------------------------------------------
# 退出端点不支持时要说清楚（W3.1）
# --------------------------------------------------------------------------
def test_shutdown_hook_absent_returns_409() -> None:
    from fastapi.testclient import TestClient

    from lifeos.api import create_app

    app = create_app(token="tok")
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post("/api/v1/system/shutdown", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "unsupported"


def test_shutdown_endpoint_calls_hook() -> None:
    from fastapi.testclient import TestClient

    from lifeos.api import create_app

    app = create_app(token="tok")
    called: list[bool] = []
    app.state.lifeos.shutdown_hook = lambda: called.append(True)
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post("/api/v1/system/shutdown", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 200
    assert resp.json() == {"shutting_down": True}
    assert called == [True]


def test_shutdown_requires_token() -> None:
    from fastapi.testclient import TestClient

    from lifeos.api import create_app

    app = create_app(token="tok")
    app.state.lifeos.shutdown_hook = lambda: None
    with TestClient(app, base_url=BASE_URL) as client:
        resp = client.post("/api/v1/system/shutdown")
    assert resp.status_code == 401


# --------------------------------------------------------------------------
# 启动器文案的语言（W3.4）
# --------------------------------------------------------------------------
def test_detect_locale_prefers_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIFEOS_LOCALE", "ja")
    assert launcher._detect_locale(["zh-CN", "en", "ja", "es"]) == "ja"


def test_detect_locale_ignores_unsupported_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIFEOS_LOCALE", "fr")
    assert launcher._detect_locale(["zh-CN"]) == "zh-CN"


def test_detect_locale_maps_system_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIFEOS_LOCALE", raising=False)
    monkeypatch.setattr("locale.getlocale", lambda *a, **k: ("ja_JP", "UTF-8"))
    assert launcher._detect_locale(["zh-CN", "en", "ja", "es"]) == "ja"


def test_detect_locale_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIFEOS_LOCALE", raising=False)
    monkeypatch.setattr("locale.getlocale", lambda *a, **k: (None, None))
    assert launcher._detect_locale(["zh-CN", "en"]) == "zh-CN"


def test_launcher_module_has_no_network_imports() -> None:
    """6.4：契约管不到启动器，这里显式守住「不引入联网出口」（验收 A2）。"""
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    for banned in ("import httpx", "import requests", "urllib.request", "import aiohttp"):
        assert banned not in source, f"启动器里出现了联网库：{banned}"


def test_module_runs_as_main() -> None:
    """双击入口是 `python -m lifeos.launcher`（W1.1），必须真的执行到 main()。

    没有 `__main__` 守卫时，解释器只会 import 一遍就静默退出（退出码 0、
    什么都没发生），而只调 main() 的单测完全发现不了 —— 这正是打包冒烟
    才暴露出来的那类问题，所以这里用子进程钉住这条命令行。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "lifeos.launcher", launcher.SMOKE_FLAG],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=CORE_ROOT,
        env=_child_env(),
    )
    assert proc.returncode == 0, proc.stderr
    assert "SQLCipher smoke OK" in proc.stdout
