"""llm：模型网关与适配器（第 9.4 节、第 9.6 节）。

这是全仓库唯一允许 import httpx 的地方，由 import-linter 契约强制。它只发送
privacy 网关算好的载荷（第 7.4 节第 7 步），并把结果回填进出站记录。

用法（第 9.2 节生成流水线的第 4 步）::

    prepared = prepare(...)                    # privacy：脱敏 + 裁剪
    log_id = record_egress(..., status="prepared")
    result = call(conn, prepared=prepared, system=system, user=user, log_id=log_id)

不可用时抛 LlmUnavailableError，调用方据此走降级模式。
"""

from .adapter import ChatMessage, Completion, complete
from .config import KEYRING_SERVICE, api_key, delete_api_key, set_api_key
from .errors import (
    REASON_MISSING_KEY,
    REASON_NOT_CONFIGURED,
    REASON_POLICY_NONE,
    REASON_UNREACHABLE,
    LlmError,
    LlmResponseError,
    LlmUnavailableError,
)
from .gateway import call
from .pricing import Pricing, estimate_cost, load_pricing

__all__ = [
    "KEYRING_SERVICE",
    "REASON_MISSING_KEY",
    "REASON_NOT_CONFIGURED",
    "REASON_POLICY_NONE",
    "REASON_UNREACHABLE",
    "ChatMessage",
    "Completion",
    "LlmError",
    "LlmResponseError",
    "LlmUnavailableError",
    "Pricing",
    "api_key",
    "call",
    "complete",
    "delete_api_key",
    "estimate_cost",
    "load_pricing",
    "set_api_key",
]
