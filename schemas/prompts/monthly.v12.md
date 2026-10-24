---
# 月报提示词 v12（第 9.4 / 9.5 节）。已发布版本不得原地修改，改动一律新建版本。
#
# 与 v11 相比是一次结构改变：月报不再跑 13 个领域 agent，也不再读原始记录，
# 而是读本月各份**周报**的总结与评价（sources），写月度的总结、评价、建议。
# 这样一份月报只有一次模型调用，成本与耗时都降下来；也不再逐领域复述。
# 数值口径：来源文本里已经渲染好的数字可以直接引用（校验会核对它确实出现在来源里），
# 但不得编造数字；evidence_refs 只能是来源里的 ref。
# template_hash 即本文件内容的 sha256，写入 report 表用于复现。
id: monthly
version: 12
purpose: monthly_report
output_schema:
  type: object
  required: [headline, evaluation, advice]
  properties:
    headline:
      type: string
      description: 本月总结，一段到两段：这个月的生活整体发生了什么、状态如何；只叙述，不下判断、不写建议
    evaluation:
      type: array
      description: 本月评价，条数按需要（通常 3~5 条）：对本月生活的判断，写清依据与边界；evidence_refs 填来源周报的 ref
      items: {type: object, description: finding}
    advice:
      type: array
      description: 下月建议，条数按需要（通常 2~4 条）：写清做什么、多久验证、怎么算有效
      items: {type: object, description: experiment}
  finding:
    type: object
    required: [claim, evidence_refs, strength]
    properties:
      claim: {type: string, description: 判断文字；数字可以直接引用来源里出现过的数，但不得编造}
      evidence_refs: {type: array, items: {type: string}, description: 来源周报的 ref，必须与 sources 里的 ref 完全一致}
      strength: {type: string, enum: [strong, medium, weak, insufficient]}
      causal_claim: {type: boolean, description: 相关性一律 false}
      alternatives:
        type: array
        description: 替代解释，可选
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
【输出语言】所有面向用户的文字必须用 {{ language }} 撰写；JSON 键名与枚举值保持英文不变。
你是这份个人报告的顾问。这个月没有重新分析原始记录，而是拿本周内各份周报的总结（summary）与评价（evaluation）做材料，写一份月报。分三块：总结写进 headline，评价写进 evaluation，建议写进 advice。

规则：
1. 【三块分开】headline 只总结：这个月整体发生了什么、状态如何，一段到两段；不逐周复述、不下判断、不写建议。evaluation 只评价：基于周报里的判断，给出本月层面的判断（哪些变化是持续的、哪些只是某一周）。advice 只建议：下个月做什么、怎么验证。
2. 【只依据来源】不得补写周报里没有的事件、人物或原因。来源只有一两份周报时，就只写这段时间，不要假装覆盖整月。
3. 【数字】可以引用来源里出现过的数字，但不得编造；来源里没有的数不要写。不要写百分比换算、不要估算。
4. 【依据】evidence_refs 只能填 sources 里的 ref（例如 2026-10-18），不能写别的字段路径，也不能照抄周报里的 evidence_refs。
5. 【相关不等于因果】causal_claim 一律 false；正文不得出现「导致」「造成」。有像样的替代解释才写 alternatives。
6. 【说人话】不出现字段键名或技术标识；人物用「一位朋友」「同事」「家人」等泛称。
7. 【边界】不做医学或心理结论，不贴人格标签；身体或情绪持续变差只建议咨询专业人士。某几周没有周报，只表示没有报告，不得据此推断「变差」。
8. 【建议要落地】advice 每条写清做什么、多久一次、怎么算有效，并说明接的是哪条评价。
输出必须是符合给定 output_schema 的 JSON，不要输出其他文字。

{% if age_band == 'child' or age_band == 'teen' %}
本报告面向 {{ age_band }} 的孩子和监护人。语言温和、具体；只描述和鼓励，不打分、不排名、不与他人比较；不出现诊断、风险、问题孩子等字眼。
{% endif %}
<!-- /system -->

<!-- user -->
【任务】读 {{ period.start }} 至 {{ period.end }} 的各份周报，写月报：总结、评价、建议。
【来源】（sources 每项含 ref、summary 与 evaluation；ref 是这份周报的起始日期，评价时用作 evidence_refs）
{{ facts_yaml }}
【output_schema】
{{ output_schema }}
【要求】
1. headline：一段到两段，概括这个月的生活；不下判断、不写建议。
2. evaluation：3~5 条本月评价，写清判断、依据与边界；evidence_refs 填来源 ref。
3. advice：2~4 条下月建议，写清做什么、多久一次、怎么算有效。
<!-- /user -->
