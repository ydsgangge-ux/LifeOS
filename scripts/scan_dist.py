#!/usr/bin/env python
"""前端构建产物扫描（第 13.4 节门禁 4）：产物里不得出现外部 URL。

前端页面自己设了严格 CSP，禁止加载任何外部脚本、字体、图片；这条门禁是它的兜底：
如果有人顺手引了一个外链 CDN，构建产物里就会留下可识别的外部主机名，这里拦住它。

允许的目标（不算外部出站）：

- ``http://127.0.0.1`` / ``http://localhost``：本机核心进程，本来就只连自己。
- ``www.w3.org``：SVG / XML 命名空间，不是网络请求，React 打包后必然出现。
- ``reactjs.org`` / ``react.dev``：React 内置错误提示里的文档链接，只是字符串，
  运行期不会去访问（何况 CSP 的 connect-src 只有 'self'，真要访问也会被拦下）。
- ``react.i18next.com`` / ``www.i18next.com``：react-i18next 在 ``if (inDevelopment)``
  守卫下写的一条开发提示里的文档链接，同理只是字符串，运行期不会访问。

用法（仓库根目录）::

    .venv\\Scripts\\python.exe scripts/scan_dist.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
DIST = _ROOT / "apps" / "web" / "dist"

ALLOWED_HOSTS = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "localhost.localdomain",
        "www.w3.org",
        "reactjs.org",
        "react.dev",
        "react.i18next.com",
        "www.i18next.com",
    }
)
URL_RE = re.compile(r"https?://([^/\s\"'`()<>\\]+)")


def scan_text(text: str) -> set[str]:
    hosts: set[str] = set()
    for match in URL_RE.finditer(text):
        host = match.group(1).split("@")[-1].split(":")[0].lower()
        if host and host not in ALLOWED_HOSTS:
            hosts.add(host)
    return hosts


def main() -> int:
    if not DIST.is_dir():
        print(f"[scan_dist] 找不到构建产物：{DIST}，先跑 npm run build", file=sys.stderr)
        return 1

    offenders: list[tuple[Path, str, str]] = []
    for path in sorted(DIST.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - 读不了就当二进制跳过
            continue
        for host in scan_text(text):
            offenders.append((path.relative_to(DIST), host, path.suffix))

    if offenders:
        print("[scan_dist] 构建产物里出现了外部 URL：", file=sys.stderr)
        for path, host, _ in offenders:
            print(f"  {path} → {host}", file=sys.stderr)
        return 1

    print("[scan_dist] 通过：产物中只有本机地址与 W3C 命名空间。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
