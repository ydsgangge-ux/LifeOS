-- 0004：自我认知校验模块（第 16 章、P1.5）。
-- 0001/0002/0003 已发布，不得修改；本文件只做新增。
-- 四表照第 16.3 节；证据的多态引用沿用 4.7 的存在性校验触发器写法。

CREATE TABLE self_claim (
  id TEXT PRIMARY KEY,
  statement TEXT NOT NULL,           -- 用户的自述，如：我执行力很强
  kind TEXT NOT NULL CHECK (kind IN
    ('strength','difficulty','value','plan_belief','change')),
  domain TEXT,
  why_note TEXT,                     -- 用户为什么这么认为
  testable_as TEXT,                  -- 可检验的行为表述，由用户确认
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN
    ('open','supported','contradicted','mixed','insufficient','retracted')),
  made_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX idx_self_claim_status ON self_claim(status) WHERE deleted_at IS NULL;

CREATE TABLE hypothesis (
  id TEXT PRIMARY KEY,
  claim_id TEXT REFERENCES self_claim(id),
  pattern_key TEXT NOT NULL,         -- 触发它的检测器，如 start_vs_finish
  statement TEXT NOT NULL,           -- 用可测量的行为陈述，不含人格词
  alternatives_json TEXT NOT NULL,   -- 至少一条替代解释
  falsifier TEXT NOT NULL,           -- 什么事实会推翻它
  strength TEXT NOT NULL CHECK (strength IN ('strong','medium','weak','insufficient')),
  coverage_json TEXT,                -- 数据覆盖：哪些周期、领域有记录，哪些缺失
  status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN
    ('proposed','user_accepted','user_rejected','testing','supported','refuted','retired')),
  proposed_at TEXT NOT NULL, reviewed_at TEXT,
  facts_snapshot_hash TEXT
);
CREATE INDEX idx_hypothesis_claim ON hypothesis(claim_id);
CREATE INDEX idx_hypothesis_status ON hypothesis(status);

CREATE TABLE hypothesis_evidence (
  id TEXT PRIMARY KEY,
  hypothesis_id TEXT NOT NULL REFERENCES hypothesis(id),
  kind TEXT NOT NULL CHECK (kind IN ('supports','counterexample','context','user_rebuttal')),
  ref_type TEXT NOT NULL CHECK (ref_type IN ('entry','metric','plan','goal')),
  ref_id TEXT NOT NULL,
  note TEXT,
  added_by TEXT NOT NULL CHECK (added_by IN ('rule','user','ai')),
  created_at TEXT NOT NULL
);
CREATE INDEX idx_evidence_hypothesis ON hypothesis_evidence(hypothesis_id);

CREATE TABLE judgment_log (          -- 系统每次判断的留痕与后续验证
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL CHECK (source IN
    ('plan_diagnosis','insight','hypothesis','recommendation')),
  source_id TEXT,
  statement TEXT NOT NULL,
  made_at TEXT NOT NULL,
  check_after TEXT,                  -- 何时回头检验
  outcome TEXT NOT NULL DEFAULT 'pending' CHECK (outcome IN
    ('pending','held','failed','inconclusive','user_overrode')),
  outcome_note TEXT, outcome_at TEXT
);
CREATE INDEX idx_judgment_check_after ON judgment_log(check_after)
  WHERE outcome = 'pending';

-- 模块设置（第 16.9 节）：默认关闭，成人档显式开启后才可用（ADR-17）。
ALTER TABLE profile_meta ADD COLUMN selfcheck_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE profile_meta ADD COLUMN selfcheck_paused INTEGER NOT NULL DEFAULT 0;
ALTER TABLE profile_meta ADD COLUMN selfcheck_weekly_limit INTEGER NOT NULL DEFAULT 2;
ALTER TABLE profile_meta ADD COLUMN selfcheck_paused_at TEXT;

-- 多态引用存在性校验（沿用 4.7）：entry/plan/goal 必须真实存在。
-- metric 是注册表里的键而非表行，故不强制。
CREATE VIEW v_evidence_targets AS
  SELECT 'entry' AS t, id FROM entry
  UNION ALL SELECT 'plan', id FROM plan
  UNION ALL SELECT 'goal', id FROM goal;

CREATE TRIGGER trg_evidence_insert_check
BEFORE INSERT ON hypothesis_evidence
BEGIN
  SELECT RAISE(ABORT, 'evidence ref missing')
  WHERE NEW.ref_type IN ('entry','plan','goal')
    AND NOT EXISTS (
      SELECT 1 FROM v_evidence_targets WHERE t = NEW.ref_type AND id = NEW.ref_id
    );
END;
