"""提示词的语言注入（P6 W5b、验收 A6）。

三件事：

1. `language` 变量已进入模板白名单，渲染时按 locale 注入**语言名**（不是 locale 码）；
2. 事实包 `profile.locale` 能一路带到 `build_context`，未知 locale 回退默认语言；
3. v2 提示词已登记 manifest sha256，且 v1 的哈希**未被改动**（ADR-07）。
"""

from __future__ import annotations

from lifeos.config import AppConfig
from lifeos.i18n import DEFAULT_LOCALE
from lifeos.releases import load_manifest
from lifeos.reporting import LANGUAGE_NAMES, language_name, load_prompt
from lifeos.reporting.prompts import TEMPLATE_VARS, build_context, render_prompt

# v1 的哈希：作为常量钉住，确保「已发布版本不得原地改」
_V1_HASHES = {
    "prompts/weekly.v1.md": "f801868e9c89b5626b8e3f596070afe1dc28ef59d52803a02e07bf75aa741397",
    "prompts/monthly.v1.md": "d5569fe5de22438cfe1f04cf8d91224305e561a5b0b5b7d8dae9651b8cff0f9a",
}


def _payload(locale: str) -> dict[str, object]:
    return {
        "facts_version": 1,
        "period": {"kind": "weekly", "start_week": 1, "end_week": 2},
        "profile": {"age_band": "adult", "locale": locale},
    }


def _render(prompt_id: str, locale: str) -> tuple[str, str]:
    template = load_prompt(prompt_id, AppConfig().prompts_dir)
    context = build_context(
        payload=_payload(locale),
        output_schema=template.output_schema,
        age_band="adult",
    )
    return render_prompt(template, context)


# ---------------------------------------------------------------------------
# 语言名映射
# ---------------------------------------------------------------------------
def test_language_var_is_whitelisted():
    assert "language" in TEMPLATE_VARS


def test_language_name_maps_four_locales():
    assert language_name("zh-CN") == "简体中文"
    assert language_name("en") == "English"
    assert language_name("ja") == "日本語"
    assert language_name("es") == "Español"


def test_unknown_locale_falls_back_to_default_language():
    assert language_name("xx-XX") == LANGUAGE_NAMES[DEFAULT_LOCALE]


def test_build_context_reads_locale_from_payload_profile():
    context = build_context(
        payload=_payload("ja"),
        output_schema={"type": "object"},
        age_band="adult",
    )
    assert context["language"] == "日本語"


# ---------------------------------------------------------------------------
# 渲染后的提示词带目标语言名
# ---------------------------------------------------------------------------
def test_rendered_prompts_carry_target_language():
    for prompt_id in ("weekly", "monthly"):
        for locale, name in LANGUAGE_NAMES.items():
            system, _user = _render(prompt_id, locale)
            assert "{{ language }}" not in system, f"{prompt_id} 占位符未渲染"
            assert name in system, f"{prompt_id}/{locale} 未注入语言名 {name}"


def test_v9_is_the_latest_version():
    for prompt_id in ("weekly", "monthly"):
        assert load_prompt(prompt_id, AppConfig().prompts_dir).version == 9
    assert load_prompt("yearly", AppConfig().prompts_dir).version == 2


# ---------------------------------------------------------------------------
# manifest 登记与旧版本保护
# ---------------------------------------------------------------------------
def test_v2_prompts_registered_with_hash():
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v2.md"] == (
        "76e557ef2f7dfafd2db71015aaf2dc8e8142d43b5e040fc2dbd3250543a14dec"
    )
    assert manifest["prompts/monthly.v2.md"] == (
        "0a05be40395ada4b91d8f553704fbec308455bfba45761641d0546880e40a63f"
    )


def test_v3_prompts_registered_with_hash():
    """v3 新增 domain_facts 规则，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v3.md"] == (
        "b54945434db197f121ff96b08415b89cc72f4f746e73e7f9aa4a1c4dfb648a7d"
    )
    assert manifest["prompts/monthly.v3.md"] == (
        "e835834faf0e429119adee95065704c0033b9dd225b103c7cb79116a9928d474"
    )


def test_v4_prompts_registered_with_hash():
    """v4 修正了实测中触发禁用词与手写数字的措辞，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v4.md"] == (
        "85d5bf2d1c89ef7106568d07f4e6fff40c07321cab9679a8c43de74da8f0edc9"
    )
    assert manifest["prompts/monthly.v4.md"] == (
        "4305e4d83e85465222c8d1408e51b9c5ca2ff30db6b08c7ef175c634bbad5e6c"
    )


def test_v5_prompts_registered_with_hash():
    """v5 补全字段路径示例，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v5.md"] == (
        "2692f4893383f43801e42a55c0b2aa197c1793f6ce9745eb767795da41d2a751"
    )
    assert manifest["prompts/monthly.v5.md"] == (
        "74898385b7cf5f0341db55931f95204ce487b4579b5b1cc10696b72ea5ea38ac"
    )


def test_v6_prompts_registered_with_hash():
    """v6 明确占位符自带单位，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v6.md"] == (
        "e7c9ad6f96de09f0ec2bc3a0ed25cd25fcd520a7119ca008c0d900b27928bcb6"
    )
    assert manifest["prompts/monthly.v6.md"] == (
        "4761a569f054be6ebe261309021b3289fafe0598b504a28a2b9a87f99ed1f51e"
    )


def test_v7_prompts_registered_with_hash():
    """v7 把字段路径规则扩展到 claim 占位符，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v7.md"] == (
        "097d82279dd315555e938747939a472bef0877ae7f7c5d24ecf21dd90f77c9b2"
    )
    assert manifest["prompts/monthly.v7.md"] == (
        "590c7030c2211eefa3e6edb513590df19a770f8c2031d9768ec7e7810103e454"
    )


def test_v8_prompts_registered_with_hash():
    """v8 加 headline 与「说人话」规则，年报 v1 与多月 history，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v8.md"] == (
        "3ac529f4b2177f9f3af4170dfd3c1552fc2c7504365cc13df17b952365259881"
    )
    assert manifest["prompts/monthly.v8.md"] == (
        "73b18398294913e4d79a6d06ce62b956847891978223733302cb2d0910fbe6ba"
    )
    assert manifest["prompts/yearly.v1.md"] == (
        "a633bdbd3a9a0bd1cd10696faac1f9210ee0d77f7bde43d5b3653dc2e9e73d72"
    )


def test_v9_prompts_registered_with_hash():
    """v9 把 headline 扩成 3~5 句的本期概览，年报同步为 v2，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v9.md"] == (
        "443c9ffed42e32da1730fc227000b020ee27a1c95d7dacb5194b39af4896b769"
    )
    assert manifest["prompts/monthly.v9.md"] == (
        "03d55da1e58fc25b1e7ac5a7af90e24d1758218255cd59da63aed2472a76f831"
    )
    assert manifest["prompts/yearly.v2.md"] == (
        "0bb6f849668ce668a8b086ce9901215e6f7b332fe8bd019084c0cbcaf7ee6896"
    )


def test_v1_hashes_untouched():
    manifest = load_manifest(AppConfig().schemas_dir)
    for rel, expected in _V1_HASHES.items():
        assert manifest[rel] == expected, f"{rel} 的哈希被改动（已发布版本不得原地改）"
