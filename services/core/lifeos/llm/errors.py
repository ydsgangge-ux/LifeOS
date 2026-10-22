"""llm 层异常（第 9.6 节降级触发点）。

故障分两类，区别只在「要不要降级」：

- LlmUnavailableError：可以降级。没网、没密钥、没配置、策略禁止出站——第 9.6 节
  要求这些情况下报告转本地降级模式，所以异常必须带 reason，界面据此说明原因。
- LlmResponseError：不该降级。HTTP 被拒、响应体不是预期的 chat completions 结构
  ——这属于配置错误或供应商问题，原样暴露比悄悄降级更安全。
"""

from __future__ import annotations

from ..domain.errors import DomainError
from ..i18n import message, render

# 降级原因，与 i18n 键 llm.unavailable.<reason> 一一对应
REASON_POLICY_NONE = "policy_none"
REASON_NOT_CONFIGURED = "not_configured"
REASON_MISSING_KEY = "missing_key"
REASON_UNREACHABLE = "unreachable"


class LlmError(DomainError):
    """llm 层基类异常。"""


class LlmUnavailableError(LlmError):
    """可降级：当前没有可用的模型出口（第 9.6 节）。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.message = message(f"llm.unavailable.{reason}")
        super().__init__(render(self.message))


class LlmResponseError(LlmError):
    """模型响应不符合契约（HTTP 拒绝或响应体结构错误）。"""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        self.message = message("llm.response.invalid", detail=detail)
        super().__init__(render(self.message))
