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
from lifeos.reporting.prompts import (
    TEMPLATE_VARS,
    build_context,
    load_domain_lens,
    render_domain_lens,
    render_prompt,
)

# v1 的哈希：作为常量钉住，确保「已发布版本不得原地改」
_V1_HASHES = {
    "prompts/weekly.v1.md": "a775033a16b9f3dbd04c886c75724cb1ecd6e7edd84930b25529c044e6d85e99",
    "prompts/monthly.v1.md": "c3aed437d3934cc6d6f973072bdbfdc50a25555ae8476ac04abe5c188912a7e1",
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


def test_latest_versions():
    """v12 起周报/月报/年报都收敛成三块；领域小结 v8 只留总结 + 评价。

    周报最新是 v13（重写：证据优先级、条数不设下限、数字规则二分、人口属性个性化）。
    """
    prompts_dir = AppConfig().prompts_dir
    assert load_prompt("weekly", prompts_dir).version == 13
    assert load_prompt("monthly", prompts_dir).version == 12
    assert load_prompt("yearly", prompts_dir).version == 6
    assert load_prompt("domain", prompts_dir).version == 8


# ---------------------------------------------------------------------------
# manifest 登记与旧版本保护
# ---------------------------------------------------------------------------
def test_v2_prompts_registered_with_hash():
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v2.md"] == (
        "1bddf0069cf34d49d173ec01b9ae53a85d76d181a4b983f4a84253b1e5629e9b"
    )
    assert manifest["prompts/monthly.v2.md"] == (
        "6b4188b5cab8b7fabb94834de791fb5cde16c9df0246c654df7298838d5d461a"
    )


def test_v3_prompts_registered_with_hash():
    """v3 新增 domain_facts 规则，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v3.md"] == (
        "9bacae2888d79a531ed9b4f41e18e3a37de62b7c02a42c37bc8820f0081c7da6"
    )
    assert manifest["prompts/monthly.v3.md"] == (
        "2b063e647efbf5ddbd3698a05d78e3cfb663019869d2377c0139cbfefdbfc745"
    )


def test_v4_prompts_registered_with_hash():
    """v4 修正了实测中触发禁用词与手写数字的措辞，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v4.md"] == (
        "ffd7b43c280ae41ea25880a1a06ed7101db363f4910eb1395aa0f2bbb0f39f1c"
    )
    assert manifest["prompts/monthly.v4.md"] == (
        "ce5fa37c9bc5c87b99ba0b982a81cf424f6cbe10e1960b2fea6ec828fb13f9b8"
    )


def test_v5_prompts_registered_with_hash():
    """v5 补全字段路径示例，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v5.md"] == (
        "8e0a9fa7c43d7d2806ef4051645232e83948389f0310a7a98565d82b169e2a06"
    )
    assert manifest["prompts/monthly.v5.md"] == (
        "b99a8988b3cb4576b76dcb5063e04079435abca6b1c4f9c9205c365bd9f54263"
    )


def test_v6_prompts_registered_with_hash():
    """v6 明确占位符自带单位，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v6.md"] == (
        "fb174c77c0b12dc6e4d7c2f52ecdaea4b6145e4656e32263e18da6720170aed4"
    )
    assert manifest["prompts/monthly.v6.md"] == (
        "a6d272cff9492e0e83f48cc79fc30914cc91e8081c2750844112ff49166f9971"
    )


def test_v7_prompts_registered_with_hash():
    """v7 把字段路径规则扩展到 claim 占位符，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v7.md"] == (
        "cbe97102b4df6bd1182ac1c0fa66eef7740b5edd02b23c879efde318429c4327"
    )
    assert manifest["prompts/monthly.v7.md"] == (
        "dbaf9f54e4388a8b1417946ed2e073599e4d000907adb82eac66cb7ced64f2e3"
    )


def test_v8_prompts_registered_with_hash():
    """v8 加 headline 与「说人话」规则，年报 v1 与多月 history，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v8.md"] == (
        "1e79898bac0c6100a120b798634fda48b3423aa1cc8d9433620dfa10be1584ae"
    )
    assert manifest["prompts/monthly.v8.md"] == (
        "cac256d932f7ef7061d6516c9401df29f601659decd7716abebf32f9d04110a8"
    )
    assert manifest["prompts/yearly.v1.md"] == (
        "f7ebbf3ad8d0c3f32bfc31944472cbbc221afa686aff6d5a5a63cc7c339f4b06"
    )


def test_v9_prompts_registered_with_hash():
    """v9 把 headline 扩成 3~5 句的本期概览，年报同步为 v2，同样必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v9.md"] == (
        "a36e9d384649a9a199c9a5de5319d9b61ace6b59d29b9528aba1ff559bcad0a5"
    )
    assert manifest["prompts/monthly.v9.md"] == (
        "e828461bbb40c39b7bd715d2ce7a5c940cab5060d25d55655ed894590961baf2"
    )
    assert manifest["prompts/yearly.v2.md"] == (
        "7e0f852bd8e72b984a5569f2e0f9c2476b199f47c919b1829c37f35f5d1d9b15"
    )


def test_v10_prompts_registered_with_hash():
    """v10 起周期提示词真正接进管线，domain v6 拆成三块输出；同样必须登记 sha256。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v10.md"] == (
        "5dd1a7f6d1fd3fc4831818a09a760d26f239733ab206a7f8f16e4f66235ddace"
    )
    assert manifest["prompts/monthly.v10.md"] == (
        "299fba4330edee2b89e19333f326f77d60afc98d7099121f03fdbb55aab0b42e"
    )
    assert manifest["prompts/yearly.v3.md"] == (
        "79c1632cc1199f2d380106d74f44c4d12c4c70c104d90444668acbfc2d0ce36d"
    )
    assert manifest["prompts/domain.v6.md"] == (
        "15af726380f6603b2ed9c77438f96239098ff0da64267d76ee3ed138aa9a5523"
    )


def test_v11_prompts_registered_with_hash():
    """v11 / domain v7 取掉句数与条数上限，篇幅交给 token 预算；同样必须登记 sha256。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v11.md"] == (
        "e22849dfa75609b17d5c819682f59cfd7b37f9ce3b3f4c4b7a82803c0519f7bd"
    )
    assert manifest["prompts/monthly.v11.md"] == (
        "9ed564688419dc3a551b6d24f58b7ba5bb094ff912227515d5514f853b4bf4c1"
    )
    assert manifest["prompts/yearly.v4.md"] == (
        "a486757e7712ff8f787c1b0de9dedaa7d7b0d17689e0e25caed518f73dc38559"
    )
    assert manifest["prompts/domain.v7.md"] == (
        "f2952bd10c8fefc7249d0d6ce8ba565216882e5c690ffe4055aea1e6a23b3415"
    )


def test_yearly_v5_prompt_registered_with_hash():
    """年报 v5 新增第 16 条，把 evidence_refs 限定在本次载荷里真实存在的路径（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/yearly.v5.md"] == (
        "8c1d8a529c13d54f4f51ba2c980c16a53a464268d853f3b92ce1963f8ccee60c"
    )


def test_v12_prompts_registered_with_hash():
    """v12 分层摘要：周报三块收敛，月报/年报只读下层报告的总结与评价（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    expected = {
        "prompts/weekly.v12.md": (
            "24bb9cc03919f5e341e3a8309784b6299502d148fabb927a90255ae2156185bf"
        ),
        "prompts/monthly.v12.md": (
            "21a4c1c4fec1ad78e4a717ebf5da037e8240199ff3abff33f4d0fba78256700b"
        ),
        "prompts/yearly.v6.md": (
            "fc8c52cef8cf29763f79a56984b22fece05838f67d23e61abe268901cffa632c"
        ),
        # domain v8：领域小结只剩总结 + 评价，不再写建议与数据缺口。
        "prompts/domain.v8.md": (
            "0f079a5778f5b2931a724a4b5c01145287de2cfd7916ffba20ed20c3ddb271fa"
        ),
    }
    for rel, digest in expected.items():
        assert manifest[rel] == digest, f"{rel} 未登记或哈希不一致"


def test_v13_weekly_registered_with_hash():
    """v13 重写周报（证据优先级、条数不设下限、数字规则二分、人口属性个性化）。

    新建版本同样必须登记 sha256（ADR-07）。
    """
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/weekly.v13.md"] == (
        "7d13965c5b0bc305f9a77d6328a9216aeaf09cdca555598bbe70ab3e7511b57c"
    )


def test_v1_hashes_untouched():
    manifest = load_manifest(AppConfig().schemas_dir)
    for rel, expected in _V1_HASHES.items():
        assert manifest[rel] == expected, f"{rel} 的哈希被改动（已发布版本不得原地改）"


# ---------------------------------------------------------------------------
# 分领域 agent：通用壳 + 领域镜头（domain.v8）
# ---------------------------------------------------------------------------
def test_domain_prompt_injects_domain_lens():
    """通用壳 + 领域镜头：每个领域只拿到自己的问题与关注点，没有镜头就只给壳。"""
    template = load_prompt("domain", AppConfig().prompts_dir)
    assert template.version == 8

    def _system(domain: str) -> str:
        lens = load_domain_lens(AppConfig().prompts_dir, domain)
        context = build_context(
            payload=_payload("zh-CN"),
            output_schema=template.output_schema,
            age_band="adult",
            domain=domain,
            domain_lens=render_domain_lens(lens),
        )
        system, _user = render_prompt(template, context)
        return system

    health = _system("health")
    plan = _system("plan")
    assert "这周的身体状态" in health
    assert "睡眠波动" in health
    assert "有没有朝目标推进" in plan
    # 镜头互不串场：健康里不谈计划完成率，计划里不谈睡眠
    assert "完成率" not in health
    assert "睡眠" not in plan
    # 镜头互不串场：收入里不谈睡眠，也不谈计划完成率
    income = _system("income")
    assert "收入记录" in income
    assert "睡眠" not in income
    assert "完成率" not in income
    assert "你是「" in income and "这个领域的顾问" in income


def test_only_some_domains_need_basics():
    """基础信息只发给「建议是否做得成」依赖处境的领域，数值型领域不重复出站。"""
    needs = {
        domain
        for domain in ("daily", "expense", "resource", "relation", "health",
                       "education", "career", "hobby", "plan", "idea", "dream",
                       "income", "goal")
        if (lens := load_domain_lens(AppConfig().prompts_dir, domain)) and lens.needs_basics
    }
    assert needs == {"health", "expense", "education", "career", "hobby", "plan", "goal"}


def test_domain_lenses_allow_single_day():
    """门槛降为「一天有记录就写」：除计划看复盘数据外，其余都该是 1 天。"""
    for domain in ("daily", "expense", "resource", "relation", "health",
                   "education", "career", "hobby", "idea", "dream", "income"):
        lens = load_domain_lens(AppConfig().prompts_dir, domain)
        assert lens is not None and lens.min_coverage_days == 1
    plan = load_domain_lens(AppConfig().prompts_dir, "plan")
    assert plan is not None and plan.min_plans_with_completion == 1
    # 目标只要「有目标」或「有检查点」就该写，不能被天数门槛挡掉
    goal = load_domain_lens(AppConfig().prompts_dir, "goal")
    assert goal is not None and goal.min_coverage_days == 0


def test_domain_v3_and_lenses_registered_with_hash():
    """通用壳与每一份领域镜头都是已发布提示词内容，必须登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    assert manifest["prompts/domain.v1.md"] == (
        "7d60484b186e9ef82e48b3dd65354036c4b75463d915f7fc8edcae9c62ca594b"
    )
    assert manifest["prompts/domain.v2.md"] == (
        "71593718746cd3a4133957876e9a0f52bf49ca6a0f1086ef3f7fe42327b1cfb0"
    )
    assert manifest["prompts/domain.v3.md"] == (
        "41ba7243402d52d0aa85bc0dde9c4e94b28a80e6a7e7086f0fba2eda49ff2ae8"
    )
    assert manifest["prompts/domain.v4.md"] == (
        "b2d595a03a128a1d61f7015cace18859b4fcaffed3b1bead0f328aee406c9ae6"
    )
    assert manifest["prompts/domain.v5.md"] == (
        "2c9238fce393535bd95109d5c2df55c3a221ea1d83b6802b70aa6f7619a4783c"
    )
    assert manifest["prompts/integration.v1.md"] == (
        "8925a461e27f34363445e244c8b1309acfd383338ec11f1cb15b8944877e646b"
    )
    assert manifest["prompts/integration.v2.md"] == (
        "f7908798cad415009e7a848cee646174763a85598e3b31585675316a2ba985fd"
    )
    assert manifest["prompts/integration.v3.md"] == (
        "dacb779b3365b798fa3378e77c58531bf61e1bbf4e225e7b1d2c97f570665541"
    )
    for domain in ("daily", "expense", "resource", "relation", "health"):
        assert load_domain_lens(AppConfig().prompts_dir, domain) is not None
        assert f"prompts/domains/{domain}.yaml" in manifest


def test_domain_lens_v2_reads_entry_content():
    """v2 镜头允许读本期条目的标题/备注/时间，并已全部登记 sha256（ADR-07）。"""
    manifest = load_manifest(AppConfig().schemas_dir)
    expected = {
        "career": "b6960afbd7b96b64bceb065c7e7b15eba71441a1d2fc114b379a2af980abb702",
        "daily": "0428adb9ce98b1301c9471f62cbdc663858553be432f18c745e130866a63dac3",
        "dream": "1a4ff58039d53917c193823a6ffcdb8614c3d732fe481663785279aa26c0df66",
        "education": "1d09ab870f89d1622e7bbb4ffad9cafabc9adae1ec237121332f7d7fbf3df532",
        "expense": "678d3748e82ac5c8c3a4d32761630127cba3b5a1e74bed0438d863171e22659c",
        "goal": "504d549d5bcce940d5dba2355d25a8178a16f179ef36b45351e57efe030401af",
        "health": "ffeabd70f40f27bdebf05ae0f3797f977d00af32c29680cab37a6006ffba2479",
        "hobby": "34e22f552b0c21363fc569637500c096291dad9186686df56bde97d204fc20b8",
        "idea": "e9c4eb76370e5c480fef00a5108010ca952d1232fffdf5909883488bbfff9d35",
        "income": "496d4c455afb77a3eb738a7c024cf5799b18d10a0bacfe03072df91f4dad802e",
        "plan": "170859ab1fc0a2ed01525c1c3a4929937bddf6dccdfcff4343a01819e18a8482",
        "relation": "1d542f2605e2dc5945ab1de2e50a3c8fd94c0bdf61f5d1c7375a63ff81efc0f8",
        "resource": "e4be1ce9612e7b78684069011b5a0f8973d4db98c52f25c7fc457bebcd5c28eb",
    }
    for domain, digest in expected.items():
        rel = f"prompts/domains/{domain}.v2.yaml"
        assert manifest[rel] == digest, f"{rel} 未登记或哈希不一致"
        lens = load_domain_lens(AppConfig().prompts_dir, domain)
        assert lens is not None
        assert any("本期" in item for item in lens.look_at), f"{domain} v2 镜头没读本期内容"
