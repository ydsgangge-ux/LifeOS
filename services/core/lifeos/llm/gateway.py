"""LLM 网关（第 7.4 节第 7 步、第 9.2 节第 4 步）：把已脱敏的载荷送出去。

它是出站管线的后半段。privacy 已经算出 `Prepared` 并落了一条 prepared 记录，
这里只做三件事：

1. 判断能不能调——策略允许、provider/model/base_url 配齐、钥匙串里有密钥。
   任何一项不满足就抛 LlmUnavailableError，报告据此走降级模式（第 9.6 节）。
2. 调用适配器发送 `prepared.payload` 对应的提示词。
3. 把结果回填到那条出站记录：sent / failed，附带 provider / model / cost_est。

回填而不是新增，是为了让「一次出站」在 egress_log 里只留一条痕迹。
本模块不碰提示词与响应内容，它们不会被写进任何表（第 7.4 节第 6 步）。
"""

from __future__ import annotations

import httpx

from ..config import AppConfig
from ..db.connection import VaultConnection
from ..privacy.audit import STATUS_FAILED, STATUS_SENT, mark_egress, record_egress
from ..privacy.gateway import Prepared
from .adapter import ChatMessage, Completion, complete
from .config import api_key
from .errors import (
    REASON_MISSING_KEY,
    REASON_NOT_CONFIGURED,
    REASON_POLICY_NONE,
    LlmError,
    LlmUnavailableError,
)
from .pricing import load_pricing
from .providers import is_local

__all__ = ["call"]


def _mark(
    conn: VaultConnection,
    log_id: str | None,
    prepared: Prepared,
    config: AppConfig,
    status: str,
    cost_est: float | None,
) -> None:
    """回填出站结果；没有现成记录时补记一条（保证任何调用都留痕）。"""
    if log_id is None:
        record_egress(
            conn,
            purpose=prepared.purpose,
            policy=prepared.policy,
            payload=prepared.payload,
            report=prepared.report,
            provider=config.llm_provider,
            model=config.llm_model,
            status=status,
            cost_est=cost_est,
        )
        return
    mark_egress(
        conn,
        log_id,
        status=status,
        provider=config.llm_provider,
        model=config.llm_model,
        cost_est=cost_est,
    )


def call(
    conn: VaultConnection,
    *,
    prepared: Prepared,
    system: str,
    user: str,
    log_id: str | None = None,
    config: AppConfig | None = None,
    transport: httpx.BaseTransport | None = None,
) -> Completion:
    """发送一次出站请求；不可用时抛 LlmUnavailableError（调用方负责降级）。

    log_id 来自 privacy 落下的 prepared 记录；为 None 时本模块自行补记一条。
    transport 只用于测试注入。
    """
    if not prepared.allowed:
        raise LlmUnavailableError(REASON_POLICY_NONE)
    cfg = config or AppConfig()
    if not cfg.llm_provider or not cfg.llm_model or not cfg.llm_base_url:
        raise LlmUnavailableError(REASON_NOT_CONFIGURED)
    key = api_key(cfg.llm_provider)
    if not key:
        # 本地推理服务（Ollama / LM Studio / vLLM）不校验密钥，但适配器要求非空
        if is_local(cfg.llm_provider):
            key = "local"
        else:
            raise LlmUnavailableError(REASON_MISSING_KEY)

    messages = [ChatMessage("system", system), ChatMessage("user", user)]
    try:
        result = complete(
            messages,
            provider=cfg.llm_provider,
            model=cfg.llm_model,
            base_url=cfg.llm_base_url,
            api_key=key,
            timeout_seconds=cfg.llm_timeout_seconds,
            max_retries=cfg.llm_max_retries,
            pricing=load_pricing(cfg.llm_pricing),
            transport=transport,
        )
    except LlmError:
        _mark(conn, log_id, prepared, cfg, STATUS_FAILED, None)
        raise
    _mark(conn, log_id, prepared, cfg, STATUS_SENT, result.cost_est)
    return result
