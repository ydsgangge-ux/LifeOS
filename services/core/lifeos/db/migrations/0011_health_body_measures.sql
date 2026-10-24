-- 0011：健康记录补身高与体重。
-- 0001–0010 已发布，不得修改。
--
-- 身高、体重作为健康详情表的普通数值列：随记录一起加密归档，
-- 也可被后续指标或报告统计直接引用（无需解析 JSON）。

ALTER TABLE health_log ADD COLUMN height_cm REAL;
ALTER TABLE health_log ADD COLUMN weight_kg REAL;
