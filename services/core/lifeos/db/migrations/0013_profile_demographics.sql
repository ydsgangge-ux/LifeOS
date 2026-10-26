-- 0013：个人人口属性（性别、身高、体重）。
-- 0001–0012 已发布，不得修改。
--
-- 自我认知与周报个性化需要「我是谁」的画像：性别、身高、体重、年龄。
-- 年龄由 profile_meta.birth_year 推算，不单列（少一份可识别面）。
-- 身高体重作为「关于我」的当前快照存这里；健康领域仍随记录存时序值
-- （0011 的 health_log.height_cm/weight_kg），两者用途不同：一个是画像，一个是趋势。
-- 只存结构化值：性别用枚举（取值在 domain/basics.py 校验），身高体重用数值，不放自由文本。
-- 这些字段会随 basics 进入出站事实包（record_fields.basics 逐字段收窄），与 0010 同一口径。

ALTER TABLE profile_meta ADD COLUMN gender TEXT;
ALTER TABLE profile_meta ADD COLUMN height_cm REAL;
ALTER TABLE profile_meta ADD COLUMN weight_kg REAL;
