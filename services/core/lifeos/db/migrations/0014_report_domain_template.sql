-- 0014：报告记录领域提示词版本与指纹。
-- 0001–0013 已发布，不得修改。
--
-- 用途：同一天重跑周报时，只要数据快照没变、领域提示词也没换版本，
-- 就直接复用上一份报告里已经跑好的领域小结，只重跑整合（总结/评价/建议），
-- 省掉 13 次领域调用。没有这两个字段的旧报告会被视为「未跑过当前领域提示词」，不参与复用。

ALTER TABLE report ADD COLUMN domain_template_version INTEGER;
ALTER TABLE report ADD COLUMN domain_template_hash TEXT;
