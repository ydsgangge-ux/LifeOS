-- 0010：个人基础信息（决策评估与自我认知的前提条件）。
-- 0001–0009 已发布，不得修改。
--
-- 只存结构化选项与短文本职位名，不放自由文本备注：
-- 这些字段会进入决策分析的事实包（decision_internal_facts），越少原文越安全。

ALTER TABLE profile_meta ADD COLUMN job_title TEXT;
ALTER TABLE profile_meta ADD COLUMN industry TEXT;
ALTER TABLE profile_meta ADD COLUMN education_level TEXT;
ALTER TABLE profile_meta ADD COLUMN work_style TEXT;
ALTER TABLE profile_meta ADD COLUMN risk_tolerance TEXT;
ALTER TABLE profile_meta ADD COLUMN family_status TEXT;
ALTER TABLE profile_meta ADD COLUMN dependents INTEGER;
ALTER TABLE profile_meta ADD COLUMN constraints_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE profile_meta ADD COLUMN basics_updated_at TEXT;
