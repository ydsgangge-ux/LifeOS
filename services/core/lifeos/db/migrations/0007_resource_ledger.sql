-- 0007：资源/资产台账（第 5 章 resource 领域）。
-- 0001–0006 已发布，不得修改。
--
-- 之前 resource_record 只有流水，没有「当前持有量」：用户每次只能重填快照。
-- 这里新增一张登记表：先登记资源账户（名称/类型/单位/初始值），之后录入只记
-- 变化量 delta，服务层把 delta 累加到 current_value，并回填本次的 value 快照。

CREATE TABLE resource_account (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT CHECK (kind IN ('time','energy','money','asset','tool','space')),
  unit TEXT,
  current_value REAL NOT NULL DEFAULT 0,
  note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

ALTER TABLE resource_record ADD COLUMN resource_id TEXT REFERENCES resource_account(id);
CREATE INDEX idx_resource_record_resource ON resource_record(resource_id);
