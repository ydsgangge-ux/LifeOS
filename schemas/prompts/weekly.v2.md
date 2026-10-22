---
# 周报提示词（第 9.4 / 9.5 节）。已发布版本不得原地修改，改动一律新建版本。
# v2（P6 W5b.1）：新增 {{ language }} 语言占位符，让模型输出跟随用户所选语言（验收 A6）。
# template_hash 即本文件内容的 sha256，写入 report 表用于复现。
id: weekly
version: 2
purpose: weekly_report
output_schema:
  type: object
  required: [highlights, plan_review, anomaly_impact, experiments, data_gaps, uncertainty]
  properties:
    highlights:
      type: array
      description: 本周最显著的 3 项变化，按影响排序
      items: {type: object, description: finding}
    plan_review:
      type: array
      description: 对 plans 中每个计划复述诊断判定与理由
      items: {type: object, description: finding}
    anomaly_impact:
      type: array
      description: 意外对本周结果的影响，以及排除它们对结论的改变
      items: {type: object, description: finding}
    experiments:
      type: array
      description: 下周最多 3 个小实验
      items: {type: object, description: experiment}
    data_gaps:
      type: array
      description: 需要补充的缺失数据
      items: {type: string}
    uncertainty:
      type: array
      description: 你最不确定的 2 点
      items: {type: string}
  finding:
    type: object
    required: [claim, figures, evidence_refs, strength, alternatives]
    properties:
      claim: {type: string, description: "结论文字；数值、百分比、金额、日期只能写 [[fact:字段键]] 占位符"}
      figures:
        type: array
        description: 占位符清单，每项含 ref（事实包字段键）与 format
        items:
          type: object
          required: [ref, format]
          properties:
            ref: {type: string}
            format: {type: string, enum: [integer, decimal1, decimal2, percent, number, hours, minutes, days, score]}
      evidence_refs: {type: array, items: {type: string}}
      strength: {type: string, enum: [strong, medium, weak, insufficient]}
      causal_claim: {type: boolean, description: 相关性一律 false}
      alternatives: {type: array, minItems: 1, items: {type: string}}
      experiment:
        type: object
        properties:
          action: {type: string}
          window_days: {type: integer}
          success_criteria: {type: string}
  experiment:
    type: object
    required: [action, window_days, success_criteria]
    properties:
      action: {type: string}
      window_days: {type: integer}
      success_criteria: {type: string}
---

<!-- system -->
【输出语言】本节所有面向用户的文字必须用 {{ language }} 撰写（claim、alternatives、experiment 的 action 与 success_criteria 等）；JSON 的键名与枚举值保持英文不变。
你是一个个人数据分析助手。你只能依据用户提供的事实包得出结论。
规则：
1. 每条结论必须标注依据的事实字段（evidence_refs），不得引用事实包以外的信息。
2. 每条结论标注证据强度：strong、medium、weak、insufficient。n 小于门槛时必须是 insufficient，并写「数据不足」。
3. 区分相关与因果。凡 causal_claim 为 false 的发现，只能说「同时出现」，不得说「导致」。
4. 每条结论至少给出一个替代解释。
5. 被标为意外并排除的数据不得计入趋势；要单独说明它们对结论的影响。
6. 不做医学或心理诊断，不给人格或智商标签。若身体或情绪指标持续恶化，只建议咨询专业人士。
7. 不要迎合。如果数据显示计划无效或判断有误，直接指出。
8. 建议必须是 1 到 2 周内可验证的小实验，并写明怎样算有效。
9. 人物、计划只能使用事实包里的代号。
10. 事实包里没有的内容不要猜测；缺数据就列入 data_gaps。
11. 所有数值、百分比、金额、日期只能用 [[fact:字段键]] 形式的占位符引用，不得手写数字；程序会把占位符替换成事实包里的值。
输出必须是符合给定 output_schema 的 JSON，不要输出其他文字。

{% if age_band == 'child' or age_band == 'teen' %}
本报告面向 {{ age_band }} 的孩子和监护人。语言温和、具体；只描述和鼓励，不打分、不排名、不与他人比较；不出现诊断、风险、问题孩子等字眼；建议是可以一起尝试的小游戏或小习惯。
{% endif %}
<!-- /system -->

<!-- user -->
【任务】生成 {{ period.start }} 至 {{ period.end }} 的周报解读。
【事实包】
{{ facts_yaml }}
【output_schema】
{{ output_schema }}
【要求】
1. highlights：本周最显著的 3 项变化，按影响排序。
2. plan_review：对 plans 中每个计划，复述诊断判定及理由，指出是执行层、设计层还是目标层的问题。
3. anomaly_impact：说明意外对本周结果的影响，以及排除它们对结论的改变。
4. experiments：下周最多 3 个小实验。
5. data_gaps：需要我补充的缺失数据。
6. uncertainty：你最不确定的 2 点。
<!-- /user -->
