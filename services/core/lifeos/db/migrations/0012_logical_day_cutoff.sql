-- 0012：逻辑日（凌晨 0–4 点归前一天）。
-- 0001–0011 已发布，不得修改。
--
-- 用户常在 0 点后才有空整理刚过完的一天。按午夜切日会把这段记录算到新的一天，
-- 前一天看起来「没记录」，周报窗口也会整体后移。这里按 4 点截止重算历史数据：
-- occurred_at 的本地小时数 < 4 时，聚合用的 occurred_date 退回前一天。
-- occurred_at 本身不动（真实时刻保留，正文里仍能看到「凌晨」）。

UPDATE entry
SET occurred_date = date(occurred_date, '-1 day')
WHERE occurred_at IS NOT NULL
  -- 必须带时刻：只写日期（如 2026-10-25）是用户显式指定，不折算。
  AND length(occurred_at) > 10
  -- 只处理聚合日期还等于真实日期、尚未重算过的行（迁移可重复执行也无副作用）。
  AND occurred_date = substr(occurred_at, 1, 10)
  AND CAST(substr(occurred_at, 12, 2) AS INTEGER) < 4;
