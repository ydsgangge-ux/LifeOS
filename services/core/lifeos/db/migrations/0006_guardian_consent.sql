-- 0006：监护人同意的记录时间（第 7.7 节）。
-- 0001–0005 已发布，不得修改；这里只新增一列。
--
-- 文档 7.7 L1040：未满 14 周岁（consent_required 为真）创建档案需要监护人确认，
-- 并记录同意时间。老档案没有该列时，state.unlock() 会补跑一次幂等迁移把它补上
-- （迁移原本只在建档案那一刻跑，见 project_memory 里踩过的坑）。

ALTER TABLE profile_meta ADD COLUMN consent_at TEXT;
