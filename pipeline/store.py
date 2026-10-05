"""SQLite state store: source rows, per-run record statuses, result caches and the call log.

Only the orchestrator's main thread writes. Every batch result is committed in one
transaction (records + cache + call row), so an interruption can never leave a
half-saved batch or double-count a record.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS inputs(
  input_sha TEXT PRIMARY KEY, path TEXT, rows INTEGER, ingested_at TEXT);
CREATE TABLE IF NOT EXISTS rows(
  input_sha TEXT, idx INTEGER, review_id TEXT, source_sha256 TEXT, text_sha TEXT,
  review_text TEXT, review_rating TEXT, review_likes TEXT, app_version TEXT, review_timestamp TEXT,
  PRIMARY KEY(input_sha, idx));
CREATE INDEX IF NOT EXISTS rows_by_id ON rows(input_sha, review_id);
CREATE INDEX IF NOT EXISTS rows_by_text ON rows(input_sha, text_sha);
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY, input_sha TEXT, input_path TEXT, created_at TEXT, config_json TEXT);
CREATE TABLE IF NOT EXISTS invocations(
  run_id TEXT, n INTEGER, phase TEXT, command TEXT, started_at TEXT, ended_at TEXT, stop_reason TEXT,
  completed_before INTEGER, completed_after INTEGER, PRIMARY KEY(run_id, n));
CREATE TABLE IF NOT EXISTS records(
  run_id TEXT, review_id TEXT, idx INTEGER, text_sha TEXT, status TEXT, label_json TEXT, label_config TEXT,
  cache_source_id TEXT, result_source TEXT, source_request_id TEXT, reason TEXT, attempts INTEGER DEFAULT 0,
  completed_invocation INTEGER, updated_at TEXT, PRIMARY KEY(run_id, review_id));
CREATE INDEX IF NOT EXISTS records_status ON records(run_id, status);
CREATE INDEX IF NOT EXISTS records_text ON records(run_id, text_sha);
CREATE TABLE IF NOT EXISTS enrich_cache(
  text_sha TEXT, label_config TEXT, label_json TEXT, source_review_id TEXT, request_id TEXT,
  run_id TEXT, created_at TEXT, PRIMARY KEY(text_sha, label_config));
CREATE TABLE IF NOT EXISTS aux_cache(
  cache_key TEXT PRIMARY KEY, role TEXT, value_json TEXT, request_id TEXT, run_id TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS calls(
  request_id TEXT PRIMARY KEY, run_id TEXT, invocation INTEGER, role TEXT, phase TEXT, model TEXT,
  label_config TEXT, tier TEXT, review_ids TEXT, n_items INTEGER, attempt INTEGER, outcome TEXT, error TEXT,
  input_tokens INTEGER, cache_creation_input_tokens INTEGER, cache_read_input_tokens INTEGER,
  output_tokens INTEGER, usage_available INTEGER, cost_usd REAL, cost_basis TEXT, reserved_usd REAL,
  started_at TEXT, ended_at TEXT, duration_s REAL, stop_reason TEXT, message_id TEXT, artifact TEXT,
  batch_id TEXT, seq INTEGER, provider_request_id TEXT, unknown_charge INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS calls_run ON calls(run_id, role);
CREATE TABLE IF NOT EXISTS batch_jobs(
  batch_id TEXT PRIMARY KEY, run_id TEXT, invocation INTEGER, created_at TEXT, status TEXT,
  requests_json TEXT, reserved_usd REAL, ended_at TEXT);
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, invocation INTEGER, at TEXT, kind TEXT, data_json TEXT);
"""


class Store:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "state.sqlite"
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # -- transactions ------------------------------------------------------
    def tx(self):
        return _Tx(self.db)

    def q(self, sql, params=()):
        return self.db.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        return self.db.execute(sql, params).fetchone()

    def scalar(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return row[0] if row else None

    # -- events --------------------------------------------------------------
    def event(self, run_id, invocation, at, kind, data):
        self.db.execute("INSERT INTO events(run_id, invocation, at, kind, data_json) VALUES (?,?,?,?,?)",
                        (run_id, invocation, at, kind, json.dumps(data, ensure_ascii=False)))

    # -- calls ---------------------------------------------------------------
    def insert_call(self, row: dict):
        cols = ",".join(row)
        self.db.execute(f"INSERT INTO calls({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))

    def update_call(self, request_id: str, **fields):
        sets = ",".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE calls SET {sets} WHERE request_id=?", (*fields.values(), request_id))

    def run_spend(self, run_id: str) -> float:
        return float(self.scalar("SELECT COALESCE(SUM(cost_usd),0) FROM calls WHERE run_id=?", (run_id,)) or 0.0)


class _Tx:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.db.execute("COMMIT")
        else:
            self.db.execute("ROLLBACK")
        return False
