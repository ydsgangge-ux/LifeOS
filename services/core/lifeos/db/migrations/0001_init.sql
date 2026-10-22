-- 0001 初始迁移
-- 依据项目构建文档 v0.2 第 4 章（含 4.7 的并入内容）。
-- 每个档案一个 vault.db（SQLCipher），因此表内不带 profile_id。
-- 已应用的迁移文件不得修改；改动一律新建版本。

-- ============================================================
-- 基础与脊柱（4.3）
-- ============================================================

CREATE TABLE profile_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  alias TEXT NOT NULL,
  birth_year INTEGER,
  age_band TEXT NOT NULL CHECK (age_band IN ('child','teen','adult','senior')),
  consent_required INTEGER NOT NULL DEFAULT 0,
  locale TEXT NOT NULL DEFAULT 'zh-CN',
  region_level TEXT,
  base_currency TEXT NOT NULL DEFAULT 'CNY',
  base_minor_exp INTEGER NOT NULL DEFAULT 2,
  modules_json TEXT NOT NULL DEFAULT '{}',
  egress_policy TEXT NOT NULL DEFAULT 'none'
    CHECK (egress_policy IN ('none','api_summary','api_extended')),
  guardian_policy_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE entry (
  id TEXT PRIMARY KEY,
  domain TEXT NOT NULL CHECK (domain IN
    ('daily','income','expense','resource','goal','relation','health',
     'education','career','hobby','plan','idea','dream')),
  occurred_at TEXT NOT NULL,
  occurred_date TEXT NOT NULL,
  title TEXT,
  note TEXT,
  tags_json TEXT,
  extra_json TEXT,
  sensitivity INTEGER NOT NULL DEFAULT 1 CHECK (sensitivity BETWEEN 0 AND 3),
  visibility TEXT NOT NULL DEFAULT 'private'
    CHECK (visibility IN ('private','guardian_visible')),
  status TEXT NOT NULL DEFAULT 'final' CHECK (status IN ('draft','final')),
  source TEXT NOT NULL DEFAULT 'manual',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT
);
CREATE INDEX idx_entry_date ON entry(occurred_date, domain) WHERE deleted_at IS NULL;
CREATE INDEX idx_entry_domain ON entry(domain, occurred_at) WHERE deleted_at IS NULL;

-- ============================================================
-- 长期对象（4.3）
-- ============================================================

CREATE TABLE person (
  id TEXT PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  relation_type TEXT,
  closeness INTEGER CHECK (closeness BETWEEN 1 AND 5),
  since_date TEXT,
  note TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE person_edge (
  a_id TEXT NOT NULL REFERENCES person(id),
  b_id TEXT NOT NULL REFERENCES person(id),
  kind TEXT NOT NULL,
  PRIMARY KEY (a_id, b_id, kind)
);

CREATE TABLE goal (
  id TEXT PRIMARY KEY,
  parent_goal_id TEXT REFERENCES goal(id),
  title TEXT NOT NULL,
  area TEXT,
  horizon TEXT CHECK (horizon IN ('1y','3y','5y','life')),
  metric_name TEXT, metric_unit TEXT,
  direction TEXT NOT NULL DEFAULT 'increase'
    CHECK (direction IN ('increase','decrease','maintain')),
  tolerance REAL,
  baseline REAL, target REAL, target_date TEXT,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','paused','achieved','dropped','revised')),
  why_note TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);

CREATE TABLE plan (
  id TEXT PRIMARY KEY,
  goal_id TEXT REFERENCES goal(id),
  title TEXT NOT NULL,
  cadence TEXT CHECK (cadence IN ('daily','weekly','monthly','once')),
  start_date TEXT, end_date TEXT,
  expected_output TEXT,
  output_metric TEXT,
  est_minutes_per_week INTEGER,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','done','dropped','replaced')),
  replaced_by TEXT REFERENCES plan(id),
  started_at TEXT, closed_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);

CREATE TABLE hobby (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE skill (id TEXT PRIMARY KEY, name TEXT NOT NULL, area TEXT, created_at TEXT NOT NULL);
CREATE TABLE skill_level (
  id TEXT PRIMARY KEY,
  skill_id TEXT NOT NULL REFERENCES skill(id),
  level REAL NOT NULL,
  assessed_at TEXT NOT NULL,
  evidence_entry_id TEXT REFERENCES entry(id)
);

-- ============================================================
-- 领域详情表（4.3，与 entry 一对一，字段全部可空）
-- ============================================================

CREATE TABLE daily_log (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  mood INTEGER CHECK (mood BETWEEN 1 AND 5),
  energy INTEGER CHECK (energy BETWEEN 1 AND 5),
  stress INTEGER CHECK (stress BETWEEN 1 AND 5),
  day_rating INTEGER CHECK (day_rating BETWEEN 1 AND 5),
  one_line TEXT
);

CREATE TABLE txn (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  amount_minor INTEGER,
  currency TEXT NOT NULL DEFAULT 'CNY',
  minor_exp INTEGER NOT NULL DEFAULT 2,
  fx_rate_to_base REAL,
  category TEXT,
  essential INTEGER,
  recurring INTEGER NOT NULL DEFAULT 0,
  budgeted INTEGER,
  person_id TEXT REFERENCES person(id)
);

CREATE TABLE resource_record (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  kind TEXT CHECK (kind IN ('time','energy','money','asset','tool','space')),
  name TEXT, value REAL, unit TEXT, delta REAL, reason TEXT
);

CREATE TABLE goal_checkpoint (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  goal_id TEXT NOT NULL REFERENCES goal(id),
  metric_value REAL,
  confidence INTEGER CHECK (confidence BETWEEN 1 AND 5),
  decision TEXT CHECK (decision IN ('keep','revise','pause','drop','achieve')),
  progress_note TEXT
);

CREATE TABLE relation_record (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  person_id TEXT NOT NULL REFERENCES person(id),
  kind TEXT CHECK (kind IN ('meet','call','message','help_given','help_received','conflict','milestone')),
  quality INTEGER CHECK (quality BETWEEN 1 AND 5),
  reciprocity INTEGER CHECK (reciprocity BETWEEN -2 AND 2),
  initiated_by TEXT CHECK (initiated_by IN ('me','them','mutual'))
);

CREATE TABLE health_log (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  sleep_hours REAL, sleep_quality INTEGER CHECK (sleep_quality BETWEEN 1 AND 5),
  exercise_minutes INTEGER, exercise_type TEXT,
  diet_quality INTEGER CHECK (diet_quality BETWEEN 1 AND 5),
  symptoms_json TEXT, measures_json TEXT, meds_note TEXT
);

CREATE TABLE education_record (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  subject TEXT,
  activity TEXT CHECK (activity IN ('class','self_study','exam','homework','project')),
  minutes INTEGER, score REAL, score_max REAL,
  skill_id TEXT REFERENCES skill(id)
);

CREATE TABLE career_record (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  activity TEXT CHECK (activity IN ('work','learning','networking','job_change','review')),
  minutes INTEGER, output_desc TEXT,
  impact INTEGER CHECK (impact BETWEEN 1 AND 5),
  opportunity INTEGER NOT NULL DEFAULT 0,
  skill_id TEXT REFERENCES skill(id)
);

CREATE TABLE hobby_record (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  hobby_id TEXT REFERENCES hobby(id),
  minutes INTEGER,
  satisfaction INTEGER CHECK (satisfaction BETWEEN 1 AND 5),
  progress_note TEXT
);

CREATE TABLE plan_record (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  plan_id TEXT NOT NULL REFERENCES plan(id),
  planned_units REAL, actual_units REAL, unit TEXT,
  output_value REAL,
  outcome TEXT CHECK (outcome IN ('done','partial','skipped','blocked'))
);
CREATE INDEX idx_plan_record_plan ON plan_record(plan_id);

CREATE TABLE idea_record (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'new'
    CHECK (status IN ('new','exploring','parked','dropped','done')),
  spawned_plan_id TEXT REFERENCES plan(id),
  spawned_goal_id TEXT REFERENCES goal(id)
);

CREATE TABLE dream_record (
  entry_id TEXT PRIMARY KEY REFERENCES entry(id) ON DELETE CASCADE,
  clarity INTEGER CHECK (clarity BETWEEN 1 AND 5),
  emotion TEXT, themes_json TEXT,
  recurring INTEGER NOT NULL DEFAULT 0
);

-- ============================================================
-- 意外、关系边、附件、主题（4.3）
-- ============================================================

CREATE TABLE anomaly (
  id TEXT PRIMARY KEY,
  entry_id TEXT NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  type TEXT NOT NULL CHECK (type IN
    ('health','finance','relationship','work','environment','plan_disruption','other')),
  impact INTEGER NOT NULL CHECK (impact BETWEEN 1 AND 5),
  one_off INTEGER NOT NULL DEFAULT 1,
  exclusion_mode TEXT NOT NULL DEFAULT 'metrics'
    CHECK (exclusion_mode IN ('none','metrics','all')),
  excluded_metrics_json TEXT,
  effect_start TEXT, effect_end TEXT,
  affected_domains_json TEXT,
  cause_note TEXT,
  resolved_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_anomaly_entry ON anomaly(entry_id);

CREATE TABLE entry_link (
  id TEXT PRIMARY KEY,
  src_type TEXT NOT NULL CHECK (src_type IN ('entry','goal','plan','person','decision')),
  src_id TEXT NOT NULL,
  dst_type TEXT NOT NULL CHECK (dst_type IN ('entry','goal','plan','person','decision')),
  dst_id TEXT NOT NULL,
  relation TEXT NOT NULL CHECK (relation IN
    ('caused_by','affects','part_of','supports_goal','inspired_by',
     'funded_by','involves_person','blocks','follow_up_of')),
  created_by TEXT NOT NULL CHECK (created_by IN ('user','rule','ai')),
  confirmed INTEGER NOT NULL DEFAULT 0,
  strength REAL,
  evidence_json TEXT,
  created_at TEXT NOT NULL,
  deleted_at TEXT,
  UNIQUE (src_type, src_id, dst_type, dst_id, relation)
);
CREATE INDEX idx_link_src ON entry_link(src_type, src_id);
CREATE INDEX idx_link_dst ON entry_link(dst_type, dst_id);

CREATE TABLE attachment (
  id TEXT PRIMARY KEY,
  entry_id TEXT REFERENCES entry(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('image','audio','doc')),
  blob_name TEXT NOT NULL,
  mime TEXT, size_bytes INTEGER, sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE theme (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  tokens_json TEXT NOT NULL,
  source_attachment_id TEXT REFERENCES attachment(id),
  is_active INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

-- ============================================================
-- 分析、报告、研究、审计（4.3）
-- ============================================================

CREATE TABLE metric_cache (
  key TEXT NOT NULL, period TEXT NOT NULL,
  value REAL, n INTEGER, meta_json TEXT, computed_at TEXT NOT NULL,
  PRIMARY KEY (key, period)
);

CREATE TABLE report (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('weekly','monthly','quarterly','yearly','decision')),
  period_start TEXT, period_end TEXT,
  status TEXT NOT NULL CHECK (status IN ('ok','degraded','failed')),
  facts_json TEXT NOT NULL,
  summary_md TEXT,
  model TEXT, template_id TEXT, template_hash TEXT,
  snapshot_hash TEXT NOT NULL,
  egress_log_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE decision_case (
  id TEXT PRIMARY KEY,
  question TEXT NOT NULL,
  domain TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN
    ('draft','assessing','researching','synthesized','decided','reviewed')),
  assessment_json TEXT,
  criteria_json TEXT,
  decision_note TEXT,
  review_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE research_task (
  id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES decision_case(id),
  pack_id TEXT NOT NULL,
  question_key TEXT NOT NULL,
  prompt_text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','pasted','ingested','verified')),
  pasted_text TEXT, pasted_at TEXT
);

CREATE TABLE evidence (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES research_task(id),
  claim TEXT NOT NULL,
  data_value TEXT, unit TEXT,
  source_name TEXT, source_url TEXT, source_date TEXT,
  source_type TEXT CHECK (source_type IN ('official','research','media','experience','unknown')),
  confidence TEXT CHECK (confidence IN ('high','medium','low')),
  conflicts_with TEXT,
  stale INTEGER NOT NULL DEFAULT 0,
  user_verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE egress_log (
  id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  purpose TEXT NOT NULL,
  provider TEXT, model TEXT, policy TEXT,
  payload_sha256 TEXT NOT NULL, payload_bytes INTEGER,
  redaction_report_json TEXT,
  status TEXT, cost_est REAL
);

-- ============================================================
-- 用户纠正、记录修订、账户与余额快照（4.7）
-- ============================================================

CREATE TABLE account (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('cash','bank','investment','credit','loan','other')),
  is_liability INTEGER NOT NULL DEFAULT 0,
  is_liquid INTEGER NOT NULL DEFAULT 0,
  include_in_runway INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'CNY',
  minor_exp INTEGER NOT NULL DEFAULT 2,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);

CREATE TABLE balance_snapshot (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES account(id),
  as_of_date TEXT NOT NULL,
  balance_minor INTEGER NOT NULL,
  source TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user','derived')),
  created_at TEXT NOT NULL,
  UNIQUE (account_id, as_of_date)
);

CREATE TABLE user_correction (
  id TEXT PRIMARY KEY,
  target_type TEXT NOT NULL CHECK (target_type IN
    ('entry','anomaly','metric','diagnosis','link','hypothesis','goal')),
  target_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN
    ('exclusion_override','verdict_reject','link_reject','data_fix','metric_context')),
  scope_json TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE entry_revision (
  id TEXT PRIMARY KEY,
  entry_id TEXT NOT NULL REFERENCES entry(id) ON DELETE CASCADE,
  rev INTEGER NOT NULL,
  changed_at TEXT NOT NULL,
  diff_json TEXT NOT NULL,
  UNIQUE (entry_id, rev)
);

-- ============================================================
-- 全文检索（4.3 说明：trigram 分词，触发器与 entry 同步）
-- ============================================================

CREATE VIRTUAL TABLE entry_fts USING fts5(
  title, note, content='entry', content_rowid='rowid', tokenize='trigram'
);

CREATE TRIGGER entry_fts_ai AFTER INSERT ON entry BEGIN
  INSERT INTO entry_fts(rowid, title, note) VALUES (new.rowid, new.title, new.note);
END;
CREATE TRIGGER entry_fts_ad AFTER DELETE ON entry BEGIN
  INSERT INTO entry_fts(entry_fts, rowid, title, note)
    VALUES ('delete', old.rowid, old.title, old.note);
END;
CREATE TRIGGER entry_fts_au AFTER UPDATE ON entry BEGIN
  INSERT INTO entry_fts(entry_fts, rowid, title, note)
    VALUES ('delete', old.rowid, old.title, old.note);
  INSERT INTO entry_fts(rowid, title, note) VALUES (new.rowid, new.title, new.note);
END;

-- ============================================================
-- 多态关系的引用完整性（4.7）
-- 注意：视图与触发器必须放在所有相关表之后。
-- ============================================================

CREATE VIEW v_link_targets AS
  SELECT 'entry' AS t, id FROM entry
  UNION ALL SELECT 'goal', id FROM goal
  UNION ALL SELECT 'plan', id FROM plan
  UNION ALL SELECT 'person', id FROM person
  UNION ALL SELECT 'decision', id FROM decision_case;

CREATE TRIGGER trg_link_insert_check
BEFORE INSERT ON entry_link
BEGIN
  SELECT RAISE(ABORT, 'link source missing')
  WHERE NOT EXISTS (SELECT 1 FROM v_link_targets WHERE t = NEW.src_type AND id = NEW.src_id);
  SELECT RAISE(ABORT, 'link target missing')
  WHERE NOT EXISTS (SELECT 1 FROM v_link_targets WHERE t = NEW.dst_type AND id = NEW.dst_id);
END;

CREATE TRIGGER trg_entry_delete_links AFTER DELETE ON entry
BEGIN
  DELETE FROM entry_link
  WHERE (src_type = 'entry' AND src_id = OLD.id)
     OR (dst_type = 'entry' AND dst_id = OLD.id);
END;

CREATE TRIGGER trg_goal_delete_links AFTER DELETE ON goal
BEGIN
  DELETE FROM entry_link
  WHERE (src_type = 'goal' AND src_id = OLD.id)
     OR (dst_type = 'goal' AND dst_id = OLD.id);
END;

CREATE TRIGGER trg_plan_delete_links AFTER DELETE ON plan
BEGIN
  DELETE FROM entry_link
  WHERE (src_type = 'plan' AND src_id = OLD.id)
     OR (dst_type = 'plan' AND dst_id = OLD.id);
END;

CREATE TRIGGER trg_person_delete_links AFTER DELETE ON person
BEGIN
  DELETE FROM entry_link
  WHERE (src_type = 'person' AND src_id = OLD.id)
     OR (dst_type = 'person' AND dst_id = OLD.id);
END;

-- 硬删除（「彻底删除」与销毁流程，第 4.7 节）：详情表 / anomaly / attachment /
-- entry_revision 的 entry_id 外键均为 ON DELETE CASCADE，会随 entry 一起清除；
-- 关系边由上面的 AFTER DELETE 触发器清除。软删除则一律由应用层写 deleted_at。
CREATE TRIGGER trg_decision_delete_links AFTER DELETE ON decision_case
BEGIN
  DELETE FROM entry_link
  WHERE (src_type = 'decision' AND src_id = OLD.id)
     OR (dst_type = 'decision' AND dst_id = OLD.id);
END;

-- user_correction 的存在性校验：仅对映射到真实表的目标类型强制。
CREATE VIEW v_correction_targets AS
  SELECT 'entry' AS t, id FROM entry
  UNION ALL SELECT 'anomaly', id FROM anomaly
  UNION ALL SELECT 'goal', id FROM goal;

CREATE TRIGGER trg_correction_insert_check
BEFORE INSERT ON user_correction
BEGIN
  SELECT RAISE(ABORT, 'correction target missing')
  WHERE NEW.target_type IN ('entry','anomaly','goal')
    AND NOT EXISTS (
      SELECT 1 FROM v_correction_targets
      WHERE t = NEW.target_type AND id = NEW.target_id
    );
END;

-- ============================================================
-- 幂等提交（第 12.1 节：POST /entries 支持 Idempotency-Key）
-- 只存请求结果，避免重复提交产生重复条目。
-- ============================================================

CREATE TABLE idempotency_key (
  key TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

