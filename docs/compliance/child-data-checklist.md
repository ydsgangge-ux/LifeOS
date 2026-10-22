# 儿童数据合规核对清单

> 依据：《人生数字化系统（LifeOS）项目构建文档》第 7.2 节（密钥体系）、第 7.7 节
> （儿童与监护人策略）、第 9.5 / 9.6 节（报告变体与降级）、第 14 章 P5 里程碑任务 5
> 「合规核对清单（对外发布前找法务确认）」。
> 维护范围：`docs/plans/P5-家庭与儿童模式.md` 的 W8 步骤。

本清单是**对外发布的前置条件**，不是「已完成」的声明。它有两部分：

- **第一部分**：本期（P5）代码中已实现、并有单测或门禁可查的项——研发自检用。
- **第二部分**：**发布前必须经法务确认的未决项**——这些不是代码问题，是承诺与责任问题，
  未经确认不得对外宣称合规。
- **第三部分**：本期里程碑内计划中、尚未落地的前端项（朗读、权限转移向导、监护人视图），
  它们各自带一条发布前必须补做的验证。

---

## 第一部分 · 本期已实现（有代码/单测可查）

| # | 核对项 | 实现位置 | 证据（可复现） | 状态 |
| --- | --- | --- | --- | --- |
| 1 | 未满 14 周岁创建档案**必须**记录监护人同意 | `services/core/lifeos/domain/profiles.py`（`create` 对 `age_band='child'` 强制 `consent_required`，未勾选抛 `GuardianConsentError`） | `tests/test_guardian.py::test_child_creation_without_consent_fails` | 已实现 |
| 2 | 同意**时间**被持久记录 | `services/core/lifeos/db/migrations/0006_guardian_consent.sql`（`profile_meta.consent_at`） | `tests/test_guardian.py::test_child_without_birth_year_still_requires_consent`（断言 `consent_at` 非空） | 已实现 |
| 3 | `child` 档即使不填 `birth_year` 也判定为需同意 | 同上 `create` 的年龄档分支 | 同上单测 | 已实现 |
| 4 | 儿童/青少年档出站策略**永久锁死为 `none`**，创建时即锁，`PUT /egress/policy` 拒绝修改 | `services/core/lifeos/privacy/policy.py`（`LOCKED_AGE_BANDS`、`assert_policy_allowed`）、`services/core/lifeos/domain/profiles.py` | `tests/test_guardian.py::test_egress_policy_locked_at_create` | 已实现 —— **对应验收 A1** |
| 5 | 儿童档报告**永远**走本地降级路径（不调用模型、无任何内容离开设备） | `services/core/lifeos/reporting/pipeline.py`（`prepared.allowed == False` → `build_degraded`） | `tests/test_reporting.py::test_pipeline_degrades_without_egress` | 已实现 —— **对应验收 A1** |
| 6 | 监护人默认**看不到**未分享内容：条目列表/详情、报告事实包、图谱三处读路径统一过滤 | `services/core/lifeos/domain/guardian.py`（`GuardianPolicy` / `Viewer`）、`domain/entries.py`、`domain/graph.py`、`analytics/facts.py`、`api/reports.py` | `tests/test_guardian.py::test_api_read_paths_filtered_for_guardian` | 已实现 —— **对应验收 A2** |
| 7 | 「可见范围是**应用层约束**、非密码学隔离」有明确文案，并出现在隐私说明与监护人视图 | `i18n/zh-CN.yaml` 的 `privacy.guardian_scope_notice`、`guardian.viewer.limit` | `tests/test_i18n.py`（键存在性）；浏览器实测 S2/S7 | 已实现 —— **对应验收 A3** |
| 8 | **数据最小化**：监护人可见范围是领域白名单，白名单外/未分享的一律不进入读数路径 | `services/core/lifeos/analytics/facts.py`（`build_facts(..., domains=...)`）、`domain/guardian.py` | 同上 A2 单测 | 已实现 |
| 9 | 儿童报告**措辞禁令**：不得出现「评分/排名/比较他人」及诊断、医学、智商/性格标签、百分数；成人指标名里的公式与「完成率」一律换成孩子话 | `services/core/lifeos/reporting/validator.py`（`BANNED_CHILD`）、`reporting/child_filter.py`（过滤层 + `assert_child_safe` 断言 + `report.child.metric_name.*` 儿童指标名）、`apps/web/src/components/ReportReader.tsx`（儿童章节名按 `output.child` 切换） | `tests/test_reporting.py::test_build_child_report_replaces_verdict_and_drops_percent`、`::test_child_gap_line_hides_metric_key_and_status_code`、`::test_pipeline_child_report_is_filtered` | 已实现 |
| 10 | 儿童报告**不自动产出**：需监护人会话 + 显式二次确认；家长不点就不生成 | `services/core/lifeos/api/reports.py`（`create_report` 门控）、`api/models.py`（`confirm_child_report`）、`apps/web/src/components/ReportReader.tsx`（`ageBand`/`role` 决定是否可生成，监护人点「生成」后弹二次确认才带 `confirm_child_report`） | `tests/test_guardian.py::test_api_child_report_gated_by_guardian`；浏览器实测 S5（家长点按钮 → 二次确认 → 产出过滤版；孩子本人会话不出现生成按钮） | 已实现 |
| 11 | 档案**销毁**路径存在（二次确认 + 输入别名），销毁即删除该档案的加密库与备份目录 | `services/core/lifeos/domain/profiles.py`（`destroy`）、`services/core/lifeos/api/profiles.py` | `tests/test_vault_lifecycle.py` | 已实现 |
| 12 | 备份是**整体加密**的密文，且备份目录与档案目录**分离**（不同路径，避免档案损坏连带丢失） | `services/core/lifeos/...` 备份模块（`data/backups/<id>/`）、`docs/plans/P5` 风险表 | `tests/test_backup.py` | 已实现（**备份含儿童数据的复核见第二部分 #3**） |
| 13 | 未成年人**密钥体系**：监护人独立密钥包装，每角色独立 KDF 盐；撤销监护人包装不影响本人口令 | `services/core/lifeos/security/keys.py`、`domain/profiles.py` | `tests/test_guardian.py::test_guardian_wrap_round_trip`、`::test_roles_use_independent_salt`、`::test_add_and_remove_guardian` | 已实现 |
| 14 | 出站内容对儿童档**根本不存在**（无载荷可发），且出站日志只存哈希/字节数、不存内容 | `services/core/lifeos/privacy/`（审计 `egress_log`） | `tests/test_privacy.py`、`tests/test_leak_golden.py` | 已实现 |
| 15 | 启动器与打包物**未新增任何联网出口**（无更新检查、无遥测、无崩溃上报），出站仍只走 `lifeos/llm/` | `LifeOS.cmd`、`services/core/lifeos/launcher.py`、随包运行时 `runtime/python/`（P6 W1） | `import-linter`「只有 llm 可以联网」契约仍 KEPT；`scripts/scan_dist.py` 产物扫描无外部 URL；P6 验收 A2 | 已实现 —— **对应验收 A2** |
| 16 | 儿童报告的**措辞禁令在多语言下仍成立**：禁令词/因果词按 `locale` 取词表，换语言后同一条禁令不静默失效 | `services/core/lifeos/reporting/validator.py`（`BANNED_ALWAYS_BY_LOCALE`/`BANNED_CHILD_BY_LOCALE`/`CAUSAL_WORDS_BY_LOCALE`）、`reporting/pipeline.py`（`validate_output(..., locale=...)`） | `tests/test_reporting_locale.py`（四语言禁令词/因果词必检出、换语言不误伤）；`tests/test_locale.py::test_english_catalog_is_actually_translated`（`report.child.*` 无百分号） | 已实现（`ja`/`es` 本地产出语言用例待词表铺开后自动生效） |

---

## 第二部分 · 发布前必须经法务确认的未决项

> 以下各项**不是代码缺陷**，而是需要法务/合规判断的承诺与责任边界。**未确认前，
> 不得对外宣称「符合某法规」**。研发已就每项准备事实材料，法务确认后再回填结论。

| # | 未决项 | 为什么需要法务 | 研发已备事实材料 |
| --- | --- | --- | --- |
| 1 | **年龄门槛 14 周岁的适用地区与法律依据** | 「14 周岁」是构建文档 L106/L1040/L1043 设定的敏感线，是否适用于目标投放地区的法规需法务确认 | 代码按 14 岁实现（第一部分 #1、#3）；依据见构建文档第 7.7 节 |
| 2 | **监护人同意的形式与可留存性** | 本系统只在本机记录「同意时间（`consent_at`）」，无监护人身份核验、无电子签名；是否满足当地「可验证的监护人同意」要求由法务判定 | 同意仅记时间戳，不记录监护人身份信息（数据最小化取舍） |
| 3 | **备份被同步到云盘/外置介质的泄漏面** | 备份是密文，但一旦用户把备份同步到云端，儿童数据的**存放位置**已离开本机；这是用户行为，产品是否需提示/限制由法务定 | 备份整体加密（第一部分 #12）；备份目录与档案目录分离 |
| 4 | **「可见范围 ≠ 密码学隔离」的对外表述** | 应用层可见范围可被持密钥的监护人技术性绕过；对外如何如实表述（既不误导家长，也不夸大保护）需法务审批措辞 | 文案已就位（第一部分 #7），但措辞的**对外版本**待法务审 |
| 5 | **未成年人的数据保留期限与删除请求** | 「孩子成年后如何迁移/删除监护人可见范围」的默认行为，是否满足当地数据主体权利，需法务确认 | 权限转移向导（第三部分 #2）本期仅规划；14 岁触发点已定 |
| 6 | **朗读功能的语音数据边界** | 朗读走系统内置合成、不联网；但**语音输入被明确不做**（见 3.2 决策）。对外宣称「不采集语音」需法务确认表述 | 见第三部分 #1 的离线验证；决策记录见 W5b ADR |

---

## 第三部分 · 本期里程碑内计划中、尚未落地的前端项

> 这些项在排期上晚于 W3/W8（属于 W5a/W5b/W6/W7），本清单先登记验收方法，
> **落地并验证通过后才可勾掉**。

| # | 项 | 对应工作 | 状态 / 发布前必须补做的验证 |
| --- | --- | --- | --- |
| 1 | 朗读功能的**离线验证** | W5b | **已实现**：`apps/web/src/lib/readAloud.ts` + `components/ReadAloud.tsx`（走浏览器内置 `speechSynthesis`，不联网）；`canSpeak()` 在无 `speechSynthesis` 时返回 false；页面上**不存在任何语音输入入口**（语音输入已按 3.2 决定不做，见 [ADR-18](../adr/ADR-18-read-aloud.md)）。发布前需在目标浏览器断网实测 S6 |
| 2 | 权限转移向导（满 14 周岁触发） | W6 / P6 W7 | **已实现**：`components/PermissionTransferWizard.tsx`（第 1 步如实说明技术局限；挂/摘监护人、调可见范围均二次确认）。**触发提示已精确**：P6 W7 已把 `birth_year` 补进解锁响应（可选字段，缺失不影响既有契约），`App.tsx` 的 `canSuggestTransfer()` 据此按年龄精确判断是否已满 14 周岁；`birth_year` 缺失时退回启发式（`age_band!=='child'`）兜底 |
| 3 | 监护人视图与「已隐藏 N 条」计数 | W7 | **列表已实现**：`App.tsx` 记 `active.role`，`components/EntryList.tsx` 用后端 `hidden_count` 展示「另有 N 条未分享」，不显示被隐藏项标题（`entries.py` 只报数）；顶部常驻横幅含技术局限文案（`guardian.viewer.*`）。**报告阅读器无计数展示**：报告接口不返回 `hidden_count`，报告侧过滤发生在 `facts` 构建阶段、无计数回传——与构建文档「列表/报告都展示计数」的表述有差异，本期按「列表展示、报告不展示」实现 |

---

## 与构建文档的一处已知差异（本期维持现状）

构建文档 **7.7 节 L1040** 描述儿童档 `egress_policy`「固定为 `none`，**只有监护人能修改，
且需二次确认**」。而实现（`services/core/lifeos/privacy/policy.py`）把 `child`/`teen`
**完全锁死，连监护人也不能改**——实现比文档更严。

**本期维持现状（锁死）**，理由：

1. 一旦监护人能放开儿童档出站，就无法维持「儿童报告永远是降级报告」这一前提，
   W3 的整套设计（过滤层 + 家长门控）会随之变形；
2. 「儿童档不出站」是对外承诺，多一个开关就多一处需要解释的口子。

因此 **文档 L1040 的后半句在本期按「未实现」登记**。将来若要放开，需单开一期并补 ADR。

---

## 复核节奏

- **每次动到儿童/监护人相关代码**：重跑后端四门禁（`ruff` / `mypy` / `lint-imports` / `pytest`），
  确认第一部分各项单测仍绿。
- **每次对外发布前**：逐条走第二 / 第三部分，取得法务结论后再更新本文件的状态列。
