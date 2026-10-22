-- 0003：报告的溯源指纹（NFR-06、第 9.4 / 8.2 节）。
--
-- 0001 已存 model / template_id / template_hash / snapshot_hash。为了让一份报告
-- 真正可复现，还需要记下「用的是哪一版提示词」与「哪一版指标注册表」——提示词与
-- 指标都可能发布新版本，只存 hash 不便人读，只存版本号又不足以校验内容。
-- 两者都存，改动任一已发布版本都能被查出来。不修改已发布的 0001/0002。

ALTER TABLE report ADD COLUMN template_version INTEGER;
ALTER TABLE report ADD COLUMN metrics_version TEXT;
ALTER TABLE report ADD COLUMN metrics_hash TEXT;
