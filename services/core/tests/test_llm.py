"""llm 模块测试（第 9.2 节第 4 步、第 9.6 节降级、P1「成本记录」）。

三块内容：
- 适配器：超时、指数退避重试、非重试性错误直接放弃、成本估算。
- 网关：策略/配置/密钥三道闸门，以及把结果回填到 egress_log（含成本）。
- 审计不落内容：调用成功后，出站记录里只有哈希与元信息，没有提示词、没有响应。

适配器用 httpx.MockTransport 伪造服务端，sleep 换成空函数，测试不真的等退避。
"""

from __future__ import annotations

import httpx
import pytest
from conftest import FastVault
from keyring import errors as keyring_errors

from lifeos.config import AppConfig
from lifeos.i18n import t
from lifeos.llm import (
    REASON_MISSING_KEY,
    REASON_NOT_CONFIGURED,
    REASON_POLICY_NONE,
    REASON_UNREACHABLE,
    ChatMessage,
    LlmResponseError,
    LlmUnavailableError,
    Pricing,
    api_key,
    call,
    complete,
    delete_api_key,
    estimate_cost,
    load_pricing,
    set_api_key,
)
from lifeos.llm import config as llm_config
from lifeos.llm import gateway as llm_gateway
from lifeos.llm.pricing import parse_pricing
from lifeos.privacy.audit import STATUS_PREPARED, STATUS_SENT, mark_egress, record_egress
from lifeos.privacy.gateway import Prepared

PROVIDER = "openai"
MODEL = "gpt-4o-mini"
BASE_URL = "https://api.example/v1"
KEY = "sk-test-key"

OK_BODY = {
    "model": MODEL,
    "choices": [{"message": {"content": '{"ok": true}'}}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
}

MESSAGES = [ChatMessage("system", "你是个人数据分析助手。"), ChatMessage("user", "【事实包】{}")]

# egress_log 的固定列——用来证明「调用没往库里多写任何东西」
EGRESS_COLUMNS = {
    "id",
    "ts",
    "purpose",
    "provider",
    "model",
    "policy",
    "payload_sha256",
    "payload_bytes",
    "redaction_report_json",
    "status",
    "cost_est",
}


def _prepared(allowed: bool = True) -> Prepared:
    return Prepared(
        purpose="weekly_report",
        policy="api_summary",
        allowed=allowed,
        payload={"period": {"start": 0, "end": 0}},
        report={"dropped_count": 0},
        sha256="deadbeef",
        nbytes=24,
    )


def _cfg(**overrides) -> AppConfig:
    values = {
        "llm_provider": PROVIDER,
        "llm_model": MODEL,
        "llm_base_url": BASE_URL,
        "llm_max_retries": 2,
    }
    values.update(overrides)
    return AppConfig(**values)


def _complete(handler, **overrides):
    kwargs = {
        "provider": PROVIDER,
        "model": MODEL,
        "base_url": BASE_URL,
        "api_key": KEY,
        "timeout_seconds": 5,
        "max_retries": 2,
        "pricing": load_pricing(AppConfig().llm_pricing),
        "transport": httpx.MockTransport(handler),
        "sleep": lambda _seconds: None,
    }
    kwargs.update(overrides)
    return complete(MESSAGES, **kwargs)


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------
def test_complete_success_parses_and_costs() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=OK_BODY)

    result = _complete(handler)
    assert result.text == '{"ok": true}'
    assert result.model == MODEL and result.provider == PROVIDER
    assert (result.prompt_tokens, result.completion_tokens) == (1000, 500)
    assert result.attempts == 1
    # gpt-4o-mini：(1000×0.00015 + 500×0.0006) / 1000
    assert result.cost_est == pytest.approx(0.00045)

    request = seen[0]
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert request.headers["Content-Type"] == "application/json"


def test_complete_retries_transient_status() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 2:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=OK_BODY)

    result = _complete(handler)
    assert len(calls) == 2
    assert result.attempts == 2  # 第一次 503、第二次成功


def test_complete_retries_timeouts_then_gives_up() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(LlmUnavailableError) as excinfo:
        _complete(handler)
    assert excinfo.value.reason == REASON_UNREACHABLE
    assert len(calls) == 3  # 1 次 + max_retries 2 次


def test_complete_gives_up_after_max_retries_on_5xx() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, text="boom")

    with pytest.raises(LlmUnavailableError) as excinfo:
        _complete(handler)
    assert excinfo.value.reason == REASON_UNREACHABLE
    assert len(calls) == 3


def test_complete_does_not_retry_client_error() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, text="bad request")

    with pytest.raises(LlmResponseError) as excinfo:
        _complete(handler)
    assert "400" in str(excinfo.value)
    assert len(calls) == 1  # 被拒的请求不重试


def test_complete_rejects_unexpected_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(LlmResponseError):
        _complete(handler)


# ---------------------------------------------------------------------------
# 成本估算
# ---------------------------------------------------------------------------
def test_estimate_cost_and_unknown_model() -> None:
    pricing = parse_pricing(
        {"models": {"m1": {"prompt_per_1k": 1.0, "completion_per_1k": 2.0}}}
    )
    assert estimate_cost(pricing, "m1", prompt_tokens=1000, completion_tokens=1000) == 3.0
    # 表里没有的模型不猜价格
    assert estimate_cost(pricing, "m2", prompt_tokens=1000, completion_tokens=1000) is None
    assert Pricing(models={}).rates("m1") is None


def test_parse_pricing_tolerates_garbage() -> None:
    assert parse_pricing(None).models == {}
    assert parse_pricing({"models": "nope"}).models == {}
    assert parse_pricing({"models": {"m": "nope"}}).models == {}


def test_load_pricing_missing_file(tmp_path) -> None:
    pricing = load_pricing(tmp_path / "nope.yaml")
    assert pricing.rates(MODEL) is None


# ---------------------------------------------------------------------------
# 凭证：一律走 keyring
# ---------------------------------------------------------------------------
class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, name: str, value: str) -> None:
        self.store[(service, name)] = value

    def get_password(self, service: str, name: str) -> str | None:
        return self.store.get((service, name))

    def delete_password(self, service: str, name: str) -> None:
        if (service, name) not in self.store:
            raise keyring_errors.PasswordDeleteError("no such password")
        del self.store[(service, name)]


def test_api_key_roundtrip_through_keyring(monkeypatch) -> None:
    fake = _FakeKeyring()
    monkeypatch.setattr(llm_config, "keyring", fake)
    assert api_key(PROVIDER) is None
    set_api_key(PROVIDER, KEY)
    assert api_key(PROVIDER) == KEY
    delete_api_key(PROVIDER)
    assert api_key(PROVIDER) is None
    delete_api_key(PROVIDER)  # 重复删除不报错
    assert api_key("") is None


def test_api_key_returns_none_without_backend(monkeypatch) -> None:
    class _Broken:
        def get_password(self, service: str, name: str) -> str | None:
            raise keyring_errors.NoKeyringError("no backend")

    monkeypatch.setattr(llm_config, "keyring", _Broken())
    assert api_key(PROVIDER) is None


# ---------------------------------------------------------------------------
# 网关：三道闸门 + 降级
# ---------------------------------------------------------------------------
def test_call_blocks_when_policy_none(fast_vault: FastVault) -> None:
    with pytest.raises(LlmUnavailableError) as excinfo:
        call(
            fast_vault.conn,
            prepared=_prepared(allowed=False),
            system="s",
            user="u",
            config=_cfg(),
        )
    assert excinfo.value.reason == REASON_POLICY_NONE
    # 没有可用出口就不该留下任何出站痕迹
    assert fast_vault.conn.query("SELECT * FROM egress_log") == []


def test_call_requires_configuration(fast_vault: FastVault) -> None:
    with pytest.raises(LlmUnavailableError) as excinfo:
        call(
            fast_vault.conn,
            prepared=_prepared(),
            system="s",
            user="u",
            config=AppConfig(llm_provider=PROVIDER, llm_model=MODEL, llm_base_url=""),
        )
    assert excinfo.value.reason == REASON_NOT_CONFIGURED


def test_call_requires_key(fast_vault: FastVault, monkeypatch) -> None:
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: None)
    with pytest.raises(LlmUnavailableError) as excinfo:
        call(
            fast_vault.conn,
            prepared=_prepared(),
            system="s",
            user="u",
            config=_cfg(),
        )
    assert excinfo.value.reason == REASON_MISSING_KEY


def test_call_records_sent_and_cost(fast_vault: FastVault, monkeypatch) -> None:
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: KEY)
    conn = fast_vault.conn
    prepared = _prepared()
    log_id = record_egress(
        conn, purpose=prepared.purpose, policy=prepared.policy, payload=prepared.payload
    )
    result = call(
        conn,
        prepared=prepared,
        system="s",
        user="u",
        log_id=log_id,
        config=_cfg(),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=OK_BODY)),
    )
    assert result.cost_est == pytest.approx(0.00045)

    rows = conn.query("SELECT * FROM egress_log")
    assert len(rows) == 1  # 回填而不是新增
    row = dict(rows[0])
    assert set(row) == EGRESS_COLUMNS  # 库里没有多出提示词或响应
    assert row["id"] == log_id
    assert row["status"] == STATUS_SENT
    assert row["provider"] == PROVIDER and row["model"] == MODEL
    assert row["cost_est"] == pytest.approx(0.00045)


def test_call_marks_failed_when_unreachable(fast_vault: FastVault, monkeypatch) -> None:
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: KEY)
    conn = fast_vault.conn
    prepared = _prepared()
    log_id = record_egress(
        conn, purpose=prepared.purpose, policy=prepared.policy, payload=prepared.payload
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(LlmUnavailableError) as excinfo:
        call(
            conn,
            prepared=prepared,
            system="s",
            user="u",
            log_id=log_id,
            config=_cfg(),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.reason == REASON_UNREACHABLE

    row = dict(conn.query_one("SELECT * FROM egress_log WHERE id = ?", (log_id,)))
    assert row["status"] == "failed"
    assert row["cost_est"] is None


def test_call_without_log_id_still_leaves_a_trace(fast_vault: FastVault, monkeypatch) -> None:
    monkeypatch.setattr(llm_gateway, "api_key", lambda _provider: KEY)
    conn = fast_vault.conn
    call(
        conn,
        prepared=_prepared(),
        system="s",
        user="u",
        config=_cfg(),
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=OK_BODY)),
    )
    rows = conn.query("SELECT * FROM egress_log")
    assert len(rows) == 1
    assert dict(rows[0])["status"] == STATUS_SENT


def test_prepared_record_is_the_one_that_gets_marked(fast_vault: FastVault) -> None:
    """预览留下的 prepared 记录，就是调用后被回填成 sent 的那一条。"""
    conn = fast_vault.conn
    prepared = _prepared()
    log_id = record_egress(
        conn, purpose=prepared.purpose, policy=prepared.policy, payload=prepared.payload
    )
    assert dict(conn.query_one("SELECT * FROM egress_log"))["status"] == STATUS_PREPARED
    mark_egress(conn, log_id, status=STATUS_SENT, provider=PROVIDER, model=MODEL)
    row = dict(conn.query_one("SELECT * FROM egress_log WHERE id = ?", (log_id,)))
    assert row["status"] == STATUS_SENT


# ---------------------------------------------------------------------------
# 文案与错误
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "reason",
    [REASON_POLICY_NONE, REASON_NOT_CONFIGURED, REASON_MISSING_KEY, REASON_UNREACHABLE],
)
def test_unavailable_reasons_have_catalog_entries(reason: str) -> None:
    key = f"llm.unavailable.{reason}"
    text = t(key)
    assert text != key
    exc = LlmUnavailableError(reason)
    assert exc.message == {"key": key, "params": {}}
    assert str(exc) == text


def test_response_error_carries_detail_in_catalog() -> None:
    exc = LlmResponseError("HTTP 400")
    assert t("llm.response.invalid", detail="HTTP 400") == str(exc)
