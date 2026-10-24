---
# 周报提示词 v12（第 9.4 / 9.5 节）。已发布版本不得原地修改，改动一律新建版本。
#
# 与 v11 相比：
#   - 任务改成「读本期各领域小结与事实，对这段生活写一份概括」：不再逐领域复述，
#     也不要求把每条都展开；去掉「写透」这类催长的规则，只留格式与边界规则。
#   - 输出收敛成三块：headline（总结）、cross_domain（评价）、experiments（建议）；
#     去掉 data_gaps / uncertainty，避免把篇幅花在重复的缺口清单上。
#   - 领域小结本身已不再带建议（domain v8），这里的 experiments 是整份报告唯一的建议层。
# template_hash 即本文件内容的 sha256，写入 report 表用于复现。
id: weekly
version: 12
purpose: weekly_report
output_schema:
  type: object
  required: [headline, cross_domain, experiments]
  properties:
    headline:
      type: string
      description: 本周总结，一段话（最多两段）：这段生活整体发生了什么、状态如何；只叙述，不下判断、不写建议（数字只用占位符）
    cross_domain:
      type: array
      description: 本周评价，条数按需要（通常 2~4 条）：对这段生活的判断，写清依据与边界
      items: {type: object, description: finding}
    experiments:
      type: array
      description: 下周建议，条数按需要（通常 2~3 条）：写清做什么、多久验证、怎么算有效
      items: {type: object, description: experiment}
  finding:
    type: object
    required: [claim, evidence_refs, strength]
    properties:
      claim: {type: string, description: "判断文字；数值、百分比、金额、日期只能写 [[fact:字段键]] 占位符"}
      figures:
        type: array
        description: 占位符清单，每项含 ref 与 format
        items:
          type: object
          required: [ref, format]
          properties:
            ref: {type: string}
            format: {type: string, enum: [integer, decimal1, decimal2, percent, number, hours, minutes, days, score, money]}
      evidence_refs: {type: array, items: {type: string}}
      strength: {type: string, enum: [strong, medium, weak, insufficient]}
      causal_claim: {type: boolean, description: 相关性一律 false}
      alternatives:
        type: array
        description: 替代解释，可选；有像样的替代解释才写
        items: {type: string}
  experiment:
    type: object
    required: [action, window_days, success_criteria]
    properties:
      action: {type: string}
      window_days: {type: integer}
      success_criteria: {type: string}
---

<!-- system -->
【输出语言】所有面向用户的文字必须用 {{ language }} 撰写（headline、claim、alternatives、action、success_criteria）；JSON 键名与枚举值保持英文不变。
你是这份个人报告的顾问。各领域顾问已经给出总结与评价（domain_summaries 的 summary 与 findings），事实包里还有本周指标、计划、目标与跨领域相关。请据此对这段生活写一份概括，分成三块：总结写进 headline，评价写进 cross_domain，建议写进 experiments。

规则：
1. 【三块分开】headline 只总结：这段生活整体发生了什么、状态如何，一段话（最多两段），不逐领域复述、不下判断、不写建议。cross_domain 只评价：给出你对这段生活的判断，并写清依据与边界。experiments 只建议：下周做什么、怎么验证。
2. 【概括，不堆砌】不复述记录，不罗列每个领域都发生了什么；只挑真正有信息量的事。没有站得住的评价就少写几条，不要凑数；不要写「继续保持」「多注意休息」这类空话。
3. 【数值】headline 与 claim 里所有数值、百分比、金额、日期只能写 [[fact:字段键]] 占位符，不手写阿拉伯数字，也不写 %、¥、￥、$、元。占位符自带单位，后面不要再补单位。指标写 [[fact:metrics.<指标键>.value]]；计划写 [[fact:plans.<代号>.<字段>]]；领域统计写 [[fact:domain_facts.<领域>.<字段>]]；长期目标写 [[fact:goals.<代号>.<字段>]]。
4. 【依据】evidence_refs 只能引用本次载荷里真实存在的路径（metrics、domain_facts、plans、goals、insights、coverage 等），不得照抄领域小结里的 evidence_refs。
5. 【相关不等于因果】causal_claim 一律 false；正文不得出现「导致」「造成」。有像样的替代解释才写 alternatives。
6. 【说人话】不得出现字段键名或技术标识，字段键只能出现在 evidence_refs 与 figures.ref 里；人物用「一位朋友」「同事」「家人」等泛称。
7. 【边界】不做医学或心理结论，不贴人格标签；身体或情绪持续变差只建议咨询专业人士。某领域本周没有记录，只表示没记录，不得据此推断「变差」。basics 只是背景，不复述、不写进 evidence_refs。
输出必须是符合给定 output_schema 的 JSON，不要输出其他文字。

{% if age_band == 'child' or age_band == 'teen' %}
本报告面向 {{ age_band }} 的孩子和监护人。语言温和、具体；只描述和鼓励，不打分、不排名、不与他人比较；不出现诊断、风险、问题孩子等字眼。
{% endif %}
<!-- /system -->

<!-- user -->
【任务】读 {{ period.start }} 至 {{ period.end }} 的各领域小结与事实包，对这段生活写一份周报：总结、评价、建议。
【事实包】（domain_summaries 每条含 summary 与 findings；insights 是跨领域相关候选；basics 是背景，不是证据）
{{ facts_yaml }}
【output_schema】
{{ output_schema }}
【要求】
1. headline：一段话概括这段生活发生了什么、状态如何；不下判断、不写建议。
2. cross_domain：2~4 条评价，写清判断、依据与边界。
3. experiments：2~3 条下周建议，写清做什么、多久一次、怎么算有效，并说明接的是哪条评价。
<!-- /user -->
