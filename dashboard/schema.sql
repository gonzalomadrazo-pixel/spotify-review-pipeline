-- Dashboard database: processed review data, aggregates and analysis outputs of one pipeline run.
-- Portable across SQLite (local) and Postgres (deployed). Loaded by dashboard/load_db.py; read-only for the API.

CREATE TABLE IF NOT EXISTS run_info (
  run_id TEXT PRIMARY KEY,
  loaded_at TEXT NOT NULL,
  input_path TEXT,
  input_sha256 TEXT,
  label_config TEXT,
  summary_json TEXT NOT NULL,
  scope_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
  review_id TEXT PRIMARY KEY,
  idx INTEGER NOT NULL,
  review_text TEXT NOT NULL,
  review_rating TEXT,
  review_likes TEXT,
  app_version TEXT,
  review_timestamp TEXT,
  month TEXT,
  status TEXT NOT NULL,
  reason TEXT,
  topic TEXT,
  subtopic TEXT,
  intent TEXT,
  sentiment DOUBLE PRECISION,
  severity INTEGER,
  evidence_quote TEXT,
  needs_review INTEGER,
  review_reason TEXT,
  entities_json TEXT,
  label_config TEXT,
  cache_source_id TEXT,
  result_source TEXT,
  source_request_id TEXT,
  issue_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_reviews_topic ON reviews(topic);
CREATE INDEX IF NOT EXISTS ix_reviews_intent ON reviews(intent);
CREATE INDEX IF NOT EXISTS ix_reviews_severity ON reviews(severity);
CREATE INDEX IF NOT EXISTS ix_reviews_issue ON reviews(issue_id);
CREATE INDEX IF NOT EXISTS ix_reviews_month ON reviews(month);

CREATE TABLE IF NOT EXISTS issues (
  issue_id TEXT PRIMARY KEY,
  rank INTEGER NOT NULL,
  topic TEXT NOT NULL,
  title TEXT,
  summary TEXT,
  coherence TEXT,
  complaint_count INTEGER NOT NULL,
  severity_sum INTEGER NOT NULL,
  mean_severity TEXT NOT NULL,
  priority_score INTEGER NOT NULL,
  cancellation_count INTEGER NOT NULL,
  severe_count INTEGER NOT NULL,
  needs_review_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS membership (
  issue_id TEXT NOT NULL,
  review_id TEXT NOT NULL,
  PRIMARY KEY (issue_id, review_id)
);

CREATE TABLE IF NOT EXISTS area_rollup (
  area TEXT PRIMARY KEY,
  complaint_count INTEGER,
  severity_sum INTEGER,
  mean_severity TEXT,
  share_of_complaints TEXT,
  severe_count INTEGER,
  cancellation_count INTEGER,
  total_complaints INTEGER
);

CREATE TABLE IF NOT EXISTS trend_monthly (
  month TEXT NOT NULL,
  area TEXT NOT NULL,
  reviews INTEGER NOT NULL,
  complaints INTEGER NOT NULL,
  PRIMARY KEY (month, area)
);

CREATE TABLE IF NOT EXISTS facts (
  fact_id TEXT PRIMARY KEY,
  scope TEXT,
  subject TEXT,
  metric TEXT,
  value TEXT,
  display TEXT,
  formula TEXT
);

CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY,
  issue_id TEXT,
  metric TEXT,
  value TEXT
);

CREATE TABLE IF NOT EXISTS memo (
  id INTEGER PRIMARY KEY,
  markdown TEXT NOT NULL,
  status TEXT,
  model TEXT,
  label_config TEXT,
  checks_json TEXT
);

CREATE TABLE IF NOT EXISTS verification (
  review_id TEXT PRIMARY KEY,
  enrich_topic TEXT,
  verify_topic TEXT,
  enrich_intent TEXT,
  verify_intent TEXT,
  enrich_severity INTEGER,
  verify_severity INTEGER,
  disagreement INTEGER
);

CREATE TABLE IF NOT EXISTS evidence_docs (
  name TEXT PRIMARY KEY,
  json TEXT NOT NULL
);
