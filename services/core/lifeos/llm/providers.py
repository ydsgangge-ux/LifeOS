"""主流 LLM 供应商目录（非敏感项）。

只登记「怎么连」：provider 标识、默认 base_url、建议模型名、是否需要密钥。
密钥永远走系统钥匙串（见 ``config.py``），不出现在这里，也不出现在 config.toml。

所有条目都必须是 OpenAI 兼容的 ``/chat/completions`` 服务——适配器只实现了这一种
协议；本地推理服务（Ollama / LM Studio / vLLM）同样兼容，只是不需要密钥。

模型名只是下拉建议，用户可以填任意自定义模型；供应商改版后不用改代码。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    """一个可选供应商。``label_key`` 是 i18n 键，由前端本地化。"""

    id: str
    label_key: str
    default_base_url: str
    models: tuple[str, ...] = ()
    local: bool = False
    requires_key: bool = True
    docs_url: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label_key": self.label_key,
            "default_base_url": self.default_base_url,
            "models": list(self.models),
            "local": self.local,
            "requires_key": self.requires_key,
            "docs_url": self.docs_url,
        }


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        "deepseek",
        "llm.provider.deepseek",
        "https://api.deepseek.com",
        ("deepseek-v4-pro", "deepseek-flash"),
        docs_url="https://api-docs.deepseek.com",
    ),
    Provider(
        "qwen",
        "llm.provider.qwen",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ("qwen3.8-max", "qwen3.7-max", "qwen3-max", "qwen-plus"),
        docs_url="https://help.aliyun.com/zh/model-studio",
    ),
    Provider(
        "glm",
        "llm.provider.glm",
        "https://open.bigmodel.cn/api/paas/v4",
        ("glm-5.3", "glm-5", "glm-5-air"),
        docs_url="https://docs.bigmodel.cn",
    ),
    Provider(
        "mimo",
        "llm.provider.mimo",
        "https://api.xiaomimimo.com/v1",
        ("mimo-v2.6-pro", "mimo-v2.6-pro-ultraspeed", "mimo-v2.6-flash"),
        docs_url="https://mimo.mi.com/docs",
    ),
    Provider(
        "kimi",
        "llm.provider.kimi",
        "https://api.moonshot.cn/v1",
        ("kimi-k2.6", "kimi-k2-turbo-preview", "kimi-latest"),
        docs_url="https://platform.moonshot.cn/docs",
    ),
    Provider(
        "doubao",
        "llm.provider.doubao",
        "https://ark.cn-beijing.volces.com/api/v3",
        ("doubao-seed-evolving", "doubao-seed-2-1-pro-260915"),
        docs_url="https://www.volcengine.com/docs/82379",
    ),
    Provider(
        "minimax",
        "llm.provider.minimax",
        "https://api.minimax.io/v1",
        ("MiniMax-M3.1-Flash", "MiniMax-M3"),
        docs_url="https://platform.minimax.io/docs",
    ),
    Provider(
        "siliconflow",
        "llm.provider.siliconflow",
        "https://api.siliconflow.cn/v1",
        ("deepseek-ai/DeepSeek-V4-Pro-0813", "deepseek-ai/DeepSeek-V4-Flash", "Qwen/Qwen3.8-27B"),
        docs_url="https://docs.siliconflow.cn",
    ),
    Provider(
        "openai",
        "llm.provider.openai",
        "https://api.openai.com/v1",
        ("gpt-6", "gpt-5.6", "gpt-5.5"),
        docs_url="https://platform.openai.com/docs",
    ),
    Provider(
        "anthropic",
        "llm.provider.anthropic",
        "https://api.anthropic.com/v1",
        ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-4-8"),
        docs_url="https://platform.claude.com/docs/en/api/openai-sdk",
    ),
    Provider(
        "gemini",
        "llm.provider.gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        ("gemini-3.8-flash", "gemini-3.1-pro-preview", "gemini-3-flash-preview"),
        docs_url="https://ai.google.dev/gemini-api/docs/openai",
    ),
    Provider(
        "ollama",
        "llm.provider.ollama",
        "http://127.0.0.1:11434/v1",
        ("qwen3.5:9b", "deepseek-v4.1-flash", "glm-5.2", "mistral-large-4"),
        local=True,
        requires_key=False,
        docs_url="https://ollama.com",
    ),
    Provider(
        "lmstudio",
        "llm.provider.lmstudio",
        "http://127.0.0.1:1234/v1",
        (),
        local=True,
        requires_key=False,
        docs_url="https://lmstudio.ai/docs",
    ),
    Provider(
        "vllm",
        "llm.provider.vllm",
        "http://127.0.0.1:8000/v1",
        (),
        local=True,
        requires_key=False,
        docs_url="https://docs.vllm.ai",
    ),
    Provider(
        "custom",
        "llm.provider.custom",
        "",
        (),
        docs_url="",
    ),
)

_BY_ID = {provider.id: provider for provider in PROVIDERS}


def provider_by_id(provider_id: str) -> Provider | None:
    return _BY_ID.get(provider_id)


def is_local(provider_id: str) -> bool:
    """本地推理服务不需要密钥；适配器仍要求非空，调用方可用占位符。"""
    provider = _BY_ID.get(provider_id)
    return bool(provider and provider.local)
