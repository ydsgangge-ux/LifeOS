# 术语表（i18n 翻译约定）

翻译共享词表时先看这里：**同一个概念在所有语言里只用一个词**，否则会出现「一个概念多译」，
报告、界面与合规文案就会互相打架。

新增语言（如 `ja.yaml` / `es.yaml`）时，请先按本表确定核心术语，再逐键翻译；
词表的键集合必须与 `zh-CN.yaml` 完全一致，且占位符 `{name}` 逐键一致 ——
由 `services/core/tests/test_locale.py` 守卫。

| 概念 | zh-CN | en | 说明 |
| --- | --- | --- | --- |
| 档案（数据主体） | 档案 | profile | 不是「文件」；`profile_meta` 里的那条记录。别名 alias 只在本机显示 |
| 保险库（加密库文件） | 保险库 | vault | 每个档案一个 SQLCipher 库；文中「vault」特指它 |
| 解锁 / 锁定 | 解锁 / 锁定 | unlock / lock | 解锁=把 DEK 读进内存；锁定=从内存丢弃（不是「登出」） |
| 口令 | 口令 | passphrase | **不是 password**；它只用来派生 KEK 包装 DEK |
| 恢复密钥 | 恢复密钥 | recovery key | 高熵密钥，只显示一次；与口令并列的第二种凭据 |
| 密钥（加密密钥） | 加密密钥 | encryption key | DEK/KEK 的统称，界面上不对用户展开细节 |
| 出站 | 出站 | egress | 数据离开本机的唯一动作；策略三档：完全不出站 / 只出汇总 / 汇总+本期条目原文（标题、备注、时间戳） |
| 本地降级 | 本地降级模式 | local fallback mode | 没有可用 LLM 出口时，报告改由本地规则生成 |
| 隐私网关 | 隐私网关 | privacy gateway | 出站前的白名单 + 敏感扫描 |
| 事实包 | 事实包 | fact package | 给模型的、可核对的汇总事实；api_extended 下另带本期条目标题/备注（entry_texts） |
| 溯源 | 溯源 | provenance | 报告用的提示词版本 + 指标指纹，用于复现 |
| 条目 | 条目 | entry | 一条记录；软删除后可在修订里找回 |
| 意外 | 意外 | anomaly | 计划外发生且影响指标的事件（不是「异常/错误」） |
| 领域 | 领域 | domain | daily/income/expense/... 的分类维度 |
| 目标 / 计划 | 目标 / 计划 | goal / plan | goal=想去哪；plan=怎么去 |
| 回看（目标回看） | 回看 | review | 定期回看目标并决定 keep/revise/pause/drop/achieve |
| 诊断（计划去留） | 诊断 | diagnosis | 判定 keep / catchup / redesign / retire / watch |
| 自我认知校验 | 自我认知 | self-awareness check | 比较「你说的」与「记录里的行为」，只出可检验陈述 |
| 假设 / 替代解释 / 反证 | 假设 / 其他解释 / 什么会推翻它 | hypothesis / other explanations / falsifier | 自述比对的产物 |
| 判断回顾 | 系统判断回顾 | judgment review | 统计过去所有判断的落点 |
| 监护人视角 | 监护人视角 | guardian view | 应用层可见范围，**不是密码学隔离** |
| 可见范围 | 可见范围 | visible scope | 监护人能看到哪些领域的约定 |
| 权限转移 | 权限转移 | permission transfer | 挂/摘监护人包装、调可见范围 |
| 儿童档 / 青少年档 / 成人档 / 长者档 | 儿童 / 青少年 / 成人 / 长者 | child / teen / adult / senior | `age_band_child`: 四个固定值，是键不是文案 |
| 关系网络 | 关系网络 | relationship network | 人物与人物/条目之间的关联 |
| 因果链 | 因果链 | causal chain | 从一条记录沿**已确认**的关联展开前因后果，最多 6 层 |
| 待确认的关联建议 | 待确认的关联建议 | suggested link | 未确认的关联不得进入结论 |
| 决策研究 | 决策研究 | decision research | 用外部证据辅助重大决策的回路的入口 |
| 证据 | 证据 | evidence | 从提示词包回填的外部材料；未核实要降权标注 |
| 一键启动 / 启动器 | 一键启动 / 启动器 | one-click start / launcher | 双击 `LifeOS.cmd`；启动器没有界面，只弹系统对话框 |
| 核心（本地服务） | 核心 | core | 绑 127.0.0.1 的 FastAPI 进程 |
| 会话令牌 | 会话令牌 | session token | 32 字节随机串，只走 URL fragment |

## 措辞禁令（合规，必须随语言同步适配）

- **儿童档报告**：不得出现「评分、排名、完成率、百分数、诊断」，也不得拿孩子与他人比较。
  禁令词表在 `services/core/lifeos/reporting/validator.py`；新增语言时**必须补一份该语言的词表**，
  否则 P5 的合规结论在新语言下会静默失效。
- **成人报告（校验器）**：非因果结论不得使用因果措辞；不得出现事实包之外的数字、百分号与金额符号。
- **自我认知校验**：不用评价性或揣测性词（「懒」「不行」「肯定」等），只出可检验陈述。
