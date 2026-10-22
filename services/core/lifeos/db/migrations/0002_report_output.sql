-- 0002：报告的结构化输出（第 9.3 / 9.7 节）。
--
-- summary_md 只够人读；报告阅读器要展示证据强度徽标、把每条小实验一键采纳成计划，
-- 需要保留模型输出的结构（findings / experiments）。因此新增一列存放校验通过后的
-- JSON 输出，不修改已发布的 0001_init.sql。

ALTER TABLE report ADD COLUMN output_json TEXT;
