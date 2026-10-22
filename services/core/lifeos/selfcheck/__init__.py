"""自我认知校验模块（第 16 章、P1.5）。

分层：
- repository：四表读写与状态流转（第 16.3 节）
- detectors：五个确定性检测器（第 16.4 节）
- hypothesis：本地模板生成与校验器（第 16.5 / 16.6 节）
- safety：频率限制、暂停、年龄档安全阀（第 16.8 节）
- service：组装成 api 层唯一入口
"""

from __future__ import annotations

from .service import SelfCheckService

__all__ = ["SelfCheckService"]
