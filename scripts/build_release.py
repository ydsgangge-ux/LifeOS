#!/usr/bin/env python
"""组装便携分发包（P6 W1.3）：把「整仓 + 随包便携 Python」拼成 ``release/LifeOS/``。

用法（在**仓库根**执行）::

    .venv\\Scripts\\python.exe scripts/build_release.py
    .venv\\Scripts\\python.exe scripts/build_release.py --skip-install  # 复用已装的依赖
    .venv\\Scripts\\python.exe scripts/build_release.py --verify-only    # 只对已有产物跑冒烟

产出 ``release/LifeOS/`` 的结构（与源码运行时的目录关系**保持一致**，所以
[config.py](services/core/lifeos/config.py) 的 ``parents[3]`` 不用改）：::

    release/LifeOS/
      LifeOS.vbs                     <- 双击这个（.cmd 留给排障）
      services/core/lifeos/…         <- 核心包（不含 tests / 缓存）
      services/core/…                <- 其余源码（不含 pyproject.toml）
      apps/web/dist/…                <- 前端构建产物（同源托管）
      schemas/ config/ i18n/
      runtime/python/                <- python.org embeddable（python.exe / pythonw.exe）

三个最容易踩的坑，脚本会替人挡掉（见 P6 计划「事实二」）：

1. **``python3xx._pth`` 不写 ``site-packages`` 就 import 不到依赖**，报错却像是
   「依赖没装」——这里重写 ``_pth``，并把 ``services/core`` 也显式挂上，
   免得依赖调用者的当前目录；
2. **便携 Python 必须与开发 venv 同版本同架构**（``cp314-win_amd64``），
   否则 native 扩展原地 ``DLL load failed``——启动前先断言；
3. **dev 依赖与测试绝不进包**——装完依赖后逐个探测，混进去就直接失败。

「版本 + sha256 登记」写在 ``release/BUILD-INFO.json``（**不放进分发包内**：
分发包里不引入任何外部 URL 字样，免得与产物扫描的「无外部 URL」验收混淆）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from collections.abc import Sequence
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = _ROOT / "release" / "LifeOS"
CACHE_DIR = _ROOT / "release" / ".cache"
RUNTIME_DIR = PACKAGE_DIR / "runtime" / "python"
LOCK_FILE = _ROOT / "runtime-requirements.lock.txt"
BUILD_INFO = _ROOT / "release" / "BUILD-INFO.json"

# 便携 Python：版本/架构必须与开发 venv 一致（本仓库当前为 CPython 3.14.0 win-amd64）
PYTHON_VERSION = "3.14.0"
PYTHON_TAG = "314"  # pythonXXX._pth 与 pythonXXX.zip 里的那串数字
PYTHON_ARCH = "win-amd64"  # 登记用
EMBED_FILE = f"python-{PYTHON_VERSION}-embed-amd64.zip"  # python.org 上的实际文件名
EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{EMBED_FILE}"
EMBED_SHA256 = "8d4d3590c10449d78aa4375f534e6d5f3027d67fdc362dd1a882279db6f90fdf"

# 分发包里必须带上的仓库顶层目录/文件（docs/ 与 README 不进包）
TOPS = ("schemas", "config", "i18n")
# 双击入口：.vbs 用 python.exe + 隐藏窗口拉起（pythonw.exe 在本机被安全软件按
# 可执行文件粒度拦掉回环连接，会让核心永久卡在启动阶段，见 LifeOS.vbs 顶部说明）。
ENTRY = "LifeOS.vbs"
# .cmd 一并带上：命令行排障用（LifeOS.cmd debug）；
# LifeOS-debug.cmd 是双击可用的保底入口（前台运行、日志可见、不依赖 VBScript）。
ENTRY_FILES = ("LifeOS.vbs", "LifeOS.cmd", "LifeOS-debug.cmd")

# services/core 里绝不进包的东西
CORE_IGNORES = (
    "tests",
    "__pycache__",
    "*.egg-info",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".import_linter_cache",
    ".hypothesis",
    "pyproject.toml",
)

# site-packages 里绝不该出现的开发依赖（装完依赖后逐个探测）
DEV_ONLY = (
    "pytest",
    "_pytest",
    "pluggy",
    "iniconfig",
    "ruff",
    "mypy",
    "mypy_extensions",
    "hypothesis",
    "sortedcontainers",
    "import_linter",
    "grimp",
    "pyinstaller",
    "altgraph",
    "pefile",
    "rich",
    "pygments",
    "markdown_it",
)


class BuildError(RuntimeError):
    """组装过程中的可预期失败（直接打印给人看，不抛堆栈）。"""


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _step(no: int, total: int, text: str) -> None:
    print(f"[{no}/{total}] {text}", flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(cmd: Sequence[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    """跑一条命令，成功时只回 stdout，失败时把 stderr 一并带进报错。

    只回 stdout 是有原因的：SQLCipher 的探针会往 stderr 打一串**UTF-16 字节流**
    的告警（那是自检故意解密失败造成的），混进来会让「取最后一行」拿到一串 NUL。
    """
    proc = subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
    )
    enc = sys.stdout.encoding or "utf-8"
    out = (proc.stdout or b"").decode(enc, errors="replace")
    err = (proc.stderr or b"").decode(enc, errors="replace")
    if proc.returncode != 0:
        raise BuildError(
            f"命令失败（退出码 {proc.returncode}）：{' '.join(cmd)}\n"
            f"{out.strip()}\n{err.strip()}"
        )
    return out.strip()


def _check_build_machine() -> None:
    """便携 Python 的 ABI 必须与跑这个脚本的解释器一致，否则别build。"""
    want = tuple(int(x) for x in PYTHON_VERSION.split(".")[:2])
    if sys.version_info[:2] != want:
        raise BuildError(
            f"必须用 CPython {PYTHON_VERSION} 跑本脚本（当前 {platform.python_version()}）："
            "便携运行时与 native 扩展轮子（sqlcipher3 等）必须同版本同架构。"
        )
    if platform.system() != "Windows" or platform.machine() not in {"AMD64", "x86_64"}:
        raise BuildError(
            f"本脚本产出的是 Windows x64 分发包（当前 {platform.system()} {platform.machine()}）；"
            "换平台需另出 runtime-requirements.lock.txt。"
        )
    if not LOCK_FILE.is_file():
        raise BuildError(f"缺少运行时依赖锁：{LOCK_FILE}")


# --------------------------------------------------------------------------
# 各步骤
# --------------------------------------------------------------------------
def _clean(keep_site_packages: bool) -> None:
    """清空产物目录；``--skip-install`` 时先把装好的依赖借出去再还回来。

    依赖是 pip 按哈希装出来的、可复现，但重装要联网也要时间；迭代界面内容时
    没必要每次都重装，所以这里把 ``site-packages`` 临时挪到 ``release/.cache/``。
    """
    stash = CACHE_DIR / "site-packages.bak"
    site = RUNTIME_DIR / "Lib" / "site-packages"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if keep_site_packages and site.is_dir():
        if stash.exists():
            shutil.rmtree(stash)
        shutil.move(str(site), str(stash))
    if PACKAGE_DIR.exists():
        shutil.rmtree(PACKAGE_DIR)
    if keep_site_packages and stash.is_dir():
        site.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stash), str(site))


def _fetch_embed() -> Path:
    """取 python.org embeddable zip（带 sha256 校验），返回缓存路径。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / Path(EMBED_URL).name
    if cached.is_file() and _sha256(cached) == EMBED_SHA256:
        print(f"      复用缓存：{cached}")
        return cached

    print(f"      下载 {EMBED_URL}")
    tmp = cached.with_suffix(".part")
    try:
        with urllib.request.urlopen(EMBED_URL, timeout=120) as resp, tmp.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except OSError as exc:
        raise BuildError(f"下载便携 Python 失败（需要联网）：{exc}") from exc

    got = _sha256(tmp)
    if got != EMBED_SHA256:
        tmp.unlink(missing_ok=True)
        raise BuildError(
            f"便携 Python 的 sha256 不符：期望 {EMBED_SHA256}，实际 {got}。"
            "上游换过包就不要悄悄接受，先核对来源。"
        )
    tmp.replace(cached)
    return cached


def _extract_embed(zip_path: Path) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.startswith("/") or ".." in Path(name).parts:
                raise BuildError(f"压缩包里有可疑路径：{name}")
        zf.extractall(RUNTIME_DIR)


def _write_pth() -> Path:
    """重写 ``pythonXXX._pth``：这是 embeddable Python 的 sys.path，漏一条就 import 不到。

    保留首行（``pythonXXX.zip``，标准库），再显式挂上 site-packages 与 lifeos 包本体。
    """
    pth = RUNTIME_DIR / f"python{PYTHON_TAG}._pth"
    if not pth.is_file():
        raise BuildError(f"便携 Python 里没有 {pth.name}，解压结果不对。")
    zip_line = f"python{PYTHON_TAG}.zip"
    for line in pth.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.strip().startswith("#"):
            zip_line = line.strip()
            break

    lines = [
        "# LifeOS 便携运行时（P6 W1.3）。改这个文件就等于改 sys.path，逐条说明：",
        f"#   {zip_line:<20} 标准库（embeddable 自带）",
        f"#   .{' ' * 20} 运行时目录本身",
        f"#   Lib\\site-packages{' ' * 5} pip --target 装进来的运行期依赖",
        f"#   ..\\..\\services\\core{' ' * 4} lifeos 包本体，免得依赖调用者的当前目录",
        "# import site 必须保留：不带它，site-packages 不会被处理。",
        zip_line,
        ".",
        r"Lib\site-packages",
        r"..\..\services\core",
        "import site",
    ]
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pth


def _install_requirements() -> Path:
    """把锁文件里的依赖按哈希装进随包 site-packages（不装依赖的依赖）。"""
    site_packages = RUNTIME_DIR / "Lib" / "site-packages"
    if site_packages.exists():
        shutil.rmtree(site_packages)
    site_packages.mkdir(parents=True)
    try:
        _run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--require-hashes",
                "--no-deps",
                "--only-binary=:all:",
                "--no-warn-script-location",
                "--target",
                str(site_packages),
                "-r",
                str(LOCK_FILE),
            ]
        )
    except BuildError as exc:
        raise BuildError(f"按锁文件装依赖失败（需要联网）：{exc}") from exc
    return site_packages


def _copy_repo() -> list[str]:
    """把运行所需的仓库内容搬进分发包（整仓形态，目录关系不变）。"""
    dist = _ROOT / "apps" / "web" / "dist"
    if not dist.is_dir():
        raise BuildError(f"找不到前端构建产物 {dist}，先在 apps/web 里跑 npm run build。")

    copied: list[str] = []
    (PACKAGE_DIR / "services").mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _ROOT / "services" / "core",
        PACKAGE_DIR / "services" / "core",
        ignore=shutil.ignore_patterns(*CORE_IGNORES),
        dirs_exist_ok=True,
    )
    copied.append("services/core")

    (PACKAGE_DIR / "apps" / "web").mkdir(parents=True, exist_ok=True)
    shutil.copytree(dist, PACKAGE_DIR / "apps" / "web" / "dist", dirs_exist_ok=True)
    copied.append("apps/web/dist")

    for name in TOPS:
        src = _ROOT / name
        if not src.is_dir():
            raise BuildError(f"缺少必要目录 {src}")
        shutil.copytree(src, PACKAGE_DIR / name, dirs_exist_ok=True)
        copied.append(name)

    for name in ENTRY_FILES:
        entry = _ROOT / name
        if not entry.is_file():
            raise BuildError(f"缺少双击入口 {entry}")
        shutil.copy2(entry, PACKAGE_DIR / name)
        copied.append(name)
    return copied


def _orphan_caches() -> list[str]:
    """分发包里不该出现的缓存/测试目录（应当为空）。

    ``__pycache__`` **故意不算**：那是 Python 每次导入都会自己写的字节码缓存，
    用户双击跑一次就会生成，属正常现象；而且复制时已经被排除，新包里本来就没有。
    真正必须为空的是 ``tests/`` 与各家的工具缓存（它们只在开发机上存在，
    一旦被打进包就说明 exclude 漏了）。

    ``runtime/`` 也不算：site-packages 里的字节码是 pip 编译出来的正常内容。
    这里只看从仓库搬进来的那几部分。
    """
    banned = {
        "tests",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".import_linter_cache",
        ".hypothesis",
    }
    junk: list[str] = []
    for path in PACKAGE_DIR.rglob("*"):
        if not path.is_dir() or path.is_relative_to(RUNTIME_DIR):
            continue
        if path.name in banned:
            junk.append(str(path.relative_to(PACKAGE_DIR)))
    return junk


def _verify() -> list[str]:
    """组装后的自检：包里没混东西，且随包解释器真能跑起来。"""
    exe = RUNTIME_DIR / "python.exe"
    if not exe.is_file():
        raise BuildError(f"便携 Python 里没有 {exe.name}，没法自检。")

    checks: list[str] = []

    # 先看结构（这两条要在任何 import 之前跑：import 本身会写 __pycache__）
    site_packages = RUNTIME_DIR / "Lib" / "site-packages"
    leaked = sorted(
        p.name
        for p in site_packages.iterdir()
        if p.name.split(".")[0].lower().replace("-", "_") in DEV_ONLY
    )
    if leaked:
        raise BuildError(f"dev 依赖混进了分发包：{', '.join(leaked)}")
    checks.append("无 dev 依赖混入 site-packages")

    junk = _orphan_caches()
    if junk:
        raise BuildError(f"分发包里残留了缓存/测试目录（共 {len(junk)} 个）：{', '.join(junk[:5])}")
    checks.append("无 tests/缓存目录残留")

    # 再看能不能跑；不让子进程写 .pyc，免得自检把包弄"脏"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    out = _run(
        [
            str(exe),
            "-c",
            "import fastapi, uvicorn, sqlcipher3, keyring, apscheduler, jinja2, httpx, pydantic;"
            "print('runtime deps ok')",
        ],
        env=env,
    )
    checks.append(f"依赖可导入：{out.splitlines()[-1]}")

    # cwd 故意选在与 services/core 无关的地方，证明 sys.path 来自 ._pth 而不是当前目录
    out = _run(
        [str(exe), "-c", "import lifeos, lifeos.launcher; print(lifeos.__file__)"],
        cwd=_ROOT / "release",
        env=env,
    )
    checks.append(f"lifeos 包可导入（不依赖 cwd）：{out.splitlines()[-1]}")

    # 双击入口走的就是这条命令行（模块有没有 __main__ 守卫，只有跑一遍才知道）
    out = _run([str(exe), "-m", "lifeos.launcher", "--smoke-sqlcipher"], env=env)
    checks.append(f"入口 `python -m lifeos.launcher` 冒烟：{out.splitlines()[-1]}")

    warnings = _loopback()  # 环境问题，只警告不判失败
    return checks, warnings


# 本机回环探针：只做一件事 —— 建一对回环套接字。起服务（asyncio 事件循环）
# 第一步就是这个动作，所以它通不通，等价于「双击后能不能起来」。
_LOOPBACK_PROBE = (
    "import faulthandler, socket;"
    "faulthandler.dump_traceback_later(8, exit=True);"
    "socket.socketpair();"
    "print('loopback ok')"
)


def _loopback() -> list[str] | None:
    """探随包**启动器实际使用的** `python.exe` 能不能走本机回环；不能就回一条警告。

    这里踩过的坑（2026-10-20 实测，结论已修正）：真正会被安全软件按「可执行文件」
    粒度拦掉回环的是 **`pythonw.exe`**，不是 `python.exe`——同一个 `runtime\\python`
    目录下，`python.exe` 的 `127.0.0.1` connect/accept 完全正常，`pythonw.exe` 则
    100% 超时，导致 `asyncio` 的 `socket.socketpair()`（Windows 走
    `_fallback_socketpair`，内部先 listen 再 connect）永久阻塞、核心不吐握手行。
    所以双击入口已改为 `LifeOS.vbs` + `python.exe`（隐藏窗口），本探针只测这个 exe。

    这仍属于**机器状态**而不是**产物状态**，所以只警告、不返回失败码。
    """
    exe = RUNTIME_DIR / "python.exe"
    try:
        proc = subprocess.run(
            [str(exe), "-c", _LOOPBACK_PROBE],
            capture_output=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return [f"本机回环探针没能跑起来（{exe}）"]
    if "loopback ok" in (proc.stdout or b"").decode("utf-8", errors="replace"):
        return None
    return [
        "本机回环不通：随包 python.exe 发起的 127.0.0.1 连接被静默丢弃，"
        "双击后核心会卡在 asyncio 建事件循环上、永远不吐握手行（不是代码问题）。",
        f"先跑 `LifeOS.cmd debug` 看日志确认卡在哪；再考虑给 {exe} 放行"
        "（安全软件网络防护 + Windows 防火墙入站规则）。换一台没有拦截的机器通常直接可用。",
    ]


def _write_build_info(
    checks: list[str], copied: list[str], warnings: list[str]
) -> dict[str, object]:
    site_packages = RUNTIME_DIR / "Lib" / "site-packages"
    info: dict[str, object] = {
        "package": str(PACKAGE_DIR.relative_to(_ROOT)).replace("\\", "/"),
        "entry": ENTRY,
        "python": {
            "version": PYTHON_VERSION,
            "arch": PYTHON_ARCH,
            "source": EMBED_URL,
            "sha256": EMBED_SHA256,
        },
        "requirements": {
            "file": LOCK_FILE.name,
            "sha256": _sha256(LOCK_FILE),
            "installed": sorted(
                p.name for p in site_packages.iterdir() if p.is_dir() and p.name != "__pycache__"
            ),
        },
        "copied": copied,
        "checks": checks,
        "warnings": warnings,
    }
    BUILD_INFO.write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return info


def _size_of(path: Path) -> tuple[int, int]:
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    # Windows 中文控制台（GBK）打不出 ✓/⚠/U+FFFD 等符号：把它们降级成替换符，
    # 免得末尾打印把整轮 build 崩掉（其实包早就组好了）。CI 的 UTF-8 终端不受影响。
    # 直接 reconfigure（不 try/except）：本脚本总在 __main__ 下跑，stdout/stderr 是 TextIOWrapper。
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(
        prog="build_release", description="组装 release/LifeOS（P6 W1.3）"
    )
    parser.add_argument(
        "--skip-install", action="store_true", help="复用已装好的 site-packages，只重装其余内容"
    )
    parser.add_argument(
        "--verify-only", action="store_true", help="不重新组装，只对已有产物跑自检"
    )
    args = parser.parse_args(argv)

    try:
        _check_build_machine()

        if args.verify_only:
            total = 1
            _step(1, total, "自检已有产物")
            checks, warnings = _verify()
            total_files, total_bytes = _size_of(PACKAGE_DIR)
        else:
            total = 6
            _step(1, total, "清空产物目录（保留 release/.cache）")
            _clean(keep_site_packages=args.skip_install)
            _step(2, total, f"取便携 Python {PYTHON_VERSION}（{PYTHON_ARCH}）并校验 sha256")
            zip_path = _fetch_embed()
            _extract_embed(zip_path)
            _step(3, total, "重写 pythonXXX._pth（让 sys.path 认得 site-packages 与 lifeos 包）")
            _write_pth()
            if args.skip_install:
                print("      跳过依赖安装（--skip-install）")
            else:
                _step(4, total, "按 runtime-requirements.lock.txt 安装运行期依赖")
                _install_requirements()
            _step(5, total, "搬入 services / apps/web/dist / schemas / config / i18n / 启动入口脚本")
            copied = _copy_repo()
            _step(6, total, "自检")
            checks, warnings = _verify()
            total_files, total_bytes = _size_of(PACKAGE_DIR)
            info = _write_build_info(checks, copied, warnings)
            print(f"      登记写入：{BUILD_INFO.relative_to(_ROOT)}")
            print(f"      python {info['python']}")

        print("\n分发包就绪：", PACKAGE_DIR.relative_to(_ROOT))
        print(f"  {total_files} 个文件，{total_bytes / 1024 / 1024:.1f} MiB")
        for line in checks:
            print(f"  [OK] {line}")
        if warnings:
            print("\n  [!] 环境提示（不影响产物本身，但会让「双击」起不来）：")
            for line in warnings:
                print(f"    - {line}")
        print(f"\n双击运行：{PACKAGE_DIR / ENTRY}")
        return 0
    except BuildError as exc:
        print(f"\n[build_release] 失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
