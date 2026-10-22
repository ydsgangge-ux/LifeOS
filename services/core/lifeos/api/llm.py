"""模型供应商设置端点（第 9.2 节、第 12.2 节）。

只做两件事：把非敏感的 provider/model/base_url 存进 config.toml，把密钥存进系统
钥匙串。接口从不回显密钥，密钥也不会随响应、日志或配置文件离开本机。

这里不做联网「测试连接」：真实的模型调用必须经过隐私网关的出站审计，绕开它发一条
试探请求会破坏「每次出站都有记录」的承诺。保存后界面只展示本地可判定的就绪状态。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from keyring import errors as keyring_errors

from ..config import save_llm_settings
from ..i18n import t
from ..llm.config import api_key, delete_api_key, set_api_key
from ..llm.providers import PROVIDERS, provider_by_id
from .deps import get_state, require_token
from .errors import CODE_INVALID_INPUT, CODE_UNSUPPORTED, ApiError
from .models import LlmSettingsUpdate
from .state import AppState

router = APIRouter(tags=["llm"])

STATUS_READY = "ready"
STATUS_MISSING_KEY = "missing_key"
STATUS_NOT_CONFIGURED = "not_configured"


def _status(state: AppState) -> str:
    cfg = state.config
    if not cfg.llm_provider or not cfg.llm_model or not cfg.llm_base_url:
        return STATUS_NOT_CONFIGURED
    provider = provider_by_id(cfg.llm_provider)
    requires_key = provider.requires_key if provider else True
    if requires_key and not api_key(cfg.llm_provider):
        return STATUS_MISSING_KEY
    return STATUS_READY


def _payload(state: AppState) -> dict:
    cfg = state.config
    return {
        "provider": cfg.llm_provider,
        "model": cfg.llm_model,
        "base_url": cfg.llm_base_url,
        "timeout_seconds": cfg.llm_timeout_seconds,
        "max_retries": cfg.llm_max_retries,
        "api_key_set": bool(cfg.llm_provider and api_key(cfg.llm_provider)),
        "status": _status(state),
        "providers": [provider.as_dict() for provider in PROVIDERS],
    }


@router.get("/llm/settings")
def get_llm_settings(
    state: AppState = Depends(get_state), _: None = Depends(require_token)
) -> dict:
    return _payload(state)


@router.put("/llm/settings")
def put_llm_settings(
    body: LlmSettingsUpdate,
    state: AppState = Depends(get_state),
    _: None = Depends(require_token),
) -> dict:
    provider = body.provider.strip()
    model = body.model.strip()
    base_url = body.base_url.strip()

    if provider:
        if not model:
            raise ApiError(400, CODE_INVALID_INPUT, t("llm.error.model_required"))
        if not base_url:
            raise ApiError(400, CODE_INVALID_INPUT, t("llm.error.base_url_required"))
        if not base_url.startswith(("http://", "https://")):
            raise ApiError(400, CODE_INVALID_INPUT, t("llm.error.base_url_invalid"))
    else:
        # 清空 provider 等于停用模型调用：报告全部走本地降级，不再保留半套设置
        model = ""
        base_url = ""

    try:
        if body.clear_api_key and provider:
            delete_api_key(provider)
        incoming = (body.api_key or "").strip()
        if incoming and provider:
            set_api_key(provider, incoming)
    except keyring_errors.KeyringError as exc:
        raise ApiError(501, CODE_UNSUPPORTED, t("llm.error.keyring")) from exc

    save_llm_settings(
        state.config,
        provider=provider,
        model=model,
        base_url=base_url,
        timeout_seconds=body.timeout_seconds,
        max_retries=body.max_retries,
    )
    return _payload(state)
