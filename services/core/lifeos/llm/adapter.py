"""LLM 适配器（第 9.2 节第 4 步、第 9.6 节）：全仓库唯一允许 import httpx 的地方。

按 OpenAI 兼容的 chat completions 协议发请求（多数云供应商与本地推理服务都支持），
把「超时、重试、成本」收敛在这一个模块里：

- 超时由客户端统一给定（config.toml 的 llm.timeout_seconds）。
- 重试只针对瞬时故障（超时、连接失败、429、5xx），指数退避；4xx 直接放弃，
  因为重试一个被拒的请求只会浪费时间和额度。
- 成本按响应里的 token 用量与 config/llm_pricing.yaml 估算，未知模型返回 None。

换供应商时只改本模块（ADR-04）；非 OpenAI 协议需要在 _payload/_parse 上加分支。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import REASON_UNREACHABLE, LlmResponseError, LlmUnavailableError
from .pricing import Pricing, estimate_cost

_BACKOFF_BASE = 1.0  # 第 n 次重试前等 _BACKOFF_BASE * 2**(n-1) 秒
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_CHAT_PATH = "/chat/completions"
# 输出长度的唯一约束：报告不再按句数/字数限制，而是给一次调用 token 预算，
# 让总结、分析、建议三块都写得透。config.toml 的 llm.max_tokens 可覆盖。
#
# 注意：这个预算是「本次请求的总输出」，推理型模型（deepseek-flash 等）把内部
# 思考也记在 completion_tokens 里。实测 8000 的预算里七千多被思考吃掉，正文
# 只剩几百 token，JSON 直接被截断；所以默认值留出一万六，让正文拿到约八千。
DEFAULT_MAX_TOKENS = 16000


@dataclass(frozen=True)
class ChatMessage:
    role: str  # system / user
    content: str


@dataclass(frozen=True)
class Completion:
    """一次成功的模型调用。cost_est 为 None 表示价格表里没有这个模型。"""

    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_est: float | None
    attempts: int
    latency_ms: int
    # OpenAI 兼容协议的结束原因；"length" 表示预算用完、内容被截断
    finish_reason: str = ""


def _payload(model: str, messages: Sequence[ChatMessage], max_tokens: int) -> dict[str, Any]:
    # 第 9.2 节第 4 步「要求输出 JSON」：用协议层的强制格式，而不是只靠提示词。
    # max_tokens 是唯一的篇幅约束：提示词不再限制句数/字数，只给 token 预算。
    return {
        "model": model,
        "messages": [{"role": m.role, "content": m.content} for m in messages],
        "response_format": {"type": "json_object"},
        "max_tokens": max_tokens,
    }


def _parse(
    response: httpx.Response,
    *,
    provider: str,
    model: str,
    pricing: Pricing | None,
    attempts: int,
    latency_ms: int,
) -> Completion:
    try:
        data = response.json()
        choice = data["choices"][0]
        text = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LlmResponseError("响应不是预期的 chat completions 结构") from exc
    usage = data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    actual_model = str(data.get("model") or model)
    cost = (
        None
        if pricing is None
        else estimate_cost(
            pricing,
            actual_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    )
    return Completion(
        text=str(text),
        provider=provider,
        model=actual_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_est=cost,
        attempts=attempts,
        latency_ms=latency_ms,
        finish_reason=str(choice.get("finish_reason") or ""),
    )


def complete(
    messages: Sequence[ChatMessage],
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout_seconds: int,
    max_retries: int,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    pricing: Pricing | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Completion:
    """调用模型并返回结果；瞬时故障重试 max_retries 次后抛 LlmUnavailableError。

    transport / sleep 只用于测试注入（httpx.MockTransport 与 no-op 睡眠）。
    """
    url = f"{base_url.rstrip('/')}{_CHAT_PATH}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    started = time.monotonic()
    attempts = 0
    with httpx.Client(timeout=timeout_seconds, transport=transport) as client:
        for attempt in range(max_retries + 1):
            attempts = attempt + 1
            try:
                response = client.post(
                    url, json=_payload(model, messages, max_tokens), headers=headers
                )
            except httpx.TimeoutException:
                pass  # 超时算瞬时故障
            except httpx.TransportError:
                pass  # 连不上 / 断流同样算瞬时故障
            else:
                if response.status_code == 200:
                    return _parse(
                        response,
                        provider=provider,
                        model=model,
                        pricing=pricing,
                        attempts=attempts,
                        latency_ms=int((time.monotonic() - started) * 1000),
                    )
                if response.status_code not in _RETRYABLE_STATUS:
                    raise LlmResponseError(f"HTTP {response.status_code}")
            if attempt < max_retries:
                sleep(_BACKOFF_BASE * 2**attempt)
    raise LlmUnavailableError(REASON_UNREACHABLE)
