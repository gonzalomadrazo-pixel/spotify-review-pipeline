"""State store: DuckDB for ingestion, caching and stage handoffs."""
import csv
import hashlib
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Any

import duckdb


class StateStore:
    """DuckDB-backed state store for the pipeline."""

    def __init__(self, db_path: str = "data/pipeline.db"):
        """Initialize the store, creating tables if needed."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self._init_tables()

    def _init_tables(self):
        """Create tables if they don't exist."""
        # Ingestion: raw + profile
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ingestion (
                review_id TEXT PRIMARY KEY,
                review_text TEXT NOT NULL,
                review_rating TEXT NOT NULL,
                review_likes TEXT NOT NULL,
                app_version TEXT,
                review_timestamp TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Enrichment: model-generated labels + cache
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS enriched (
                review_id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                subtopic TEXT NOT NULL,
                intent TEXT NOT NULL,
                sentiment FLOAT NOT NULL,
                severity INTEGER NOT NULL,
                entities TEXT,
                evidence_quote TEXT NOT NULL,
                needs_review BOOLEAN NOT NULL,
                label_config TEXT NOT NULL,
                cache_source_id TEXT,
                enriched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (review_id) REFERENCES ingestion(review_id)
            )
        """)

        # Verification: second model check
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS verified (
                review_id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                intent TEXT NOT NULL,
                severity INTEGER NOT NULL,
                confident BOOLEAN NOT NULL,
                verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (review_id) REFERENCES ingestion(review_id)
            )
        """)

        # Grouping: issue membership
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS membership (
                issue_id TEXT NOT NULL,
                review_id TEXT NOT NULL,
                member_severity INTEGER NOT NULL,
                grouped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (issue_id, review_id),
                FOREIGN KEY (review_id) REFERENCES ingestion(review_id)
            )
        """)

        # Run metadata
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS run_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # API calls log
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS api_calls (
                request_id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                model TEXT NOT NULL,
                phase TEXT,
                review_ids TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                outcome TEXT NOT NULL,
                label_config TEXT,
                called_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def ingest_csv(self, csv_path: str) -> dict:
        """Read CSV, compute hashes, insert. Return profile."""
        csv_path = Path(csv_path)
        ingested = 0
        duplicate_ids = 0
        empty_texts = 0
        stats = {}

        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, strict=True)
            for row in reader:
                sha = self._row_hash(row)
                text_stripped = row["review_text"].strip()

                if not text_stripped:
                    empty_texts += 1

                try:
                    self.conn.execute("""
                        INSERT INTO ingestion
                        (review_id, review_text, review_rating, review_likes, app_version, review_timestamp, source_sha256)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, [
                        row["review_id"],
                        row["review_text"],
                        row["review_rating"],
                        row["review_likes"],
                        row["app_version"],
                        row["review_timestamp"],
                        sha
                    ])
                    ingested += 1
                except duckdb.IntegrityError:
                    duplicate_ids += 1

        self.conn.commit()

        # Profile
        result = self.conn.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(DISTINCT review_text) as distinct_texts,
                SUM(CASE WHEN review_text = '' THEN 1 ELSE 0 END) as empty_exact,
                COUNT(CASE WHEN app_version = '' THEN 1 END) as missing_app_version
            FROM ingestion
        """).fetchall()[0]

        return {
            "ingested": ingested,
            "duplicate_ids": duplicate_ids,
            "empty_texts": empty_texts,
            "total_rows": result[0],
            "distinct_texts": result[1],
            "empty_exact": result[2] or 0,
            "missing_app_version": result[3] or 0,
        }

    def _row_hash(self, row: dict) -> str:
        """SHA256 of canonical JSON of [id, text, rating, likes, app_version, timestamp]."""
        fields = [row["review_id"], row["review_text"], row["review_rating"],
                  row["review_likes"], row["app_version"], row["review_timestamp"]]
        canonical = json.dumps(fields, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def get_pending_reviews(self, limit: int = 50, phase: str = "initial") -> list[dict]:
        """Get reviews not yet enriched, ordered by ID."""
        query = """
            SELECT i.review_id, i.review_text
            FROM ingestion i
            LEFT JOIN enriched e ON i.review_id = e.review_id
            WHERE e.review_id IS NULL
            ORDER BY i.review_id
            LIMIT ?
        """
        rows = self.conn.execute(query, [limit]).fetchall()
        return [{"k": r[0], "text": r[1]} for r in rows]

    def save_enrichment(self, review_id: str, labels: dict, label_config: str, cache_source_id: Optional[str] = None):
        """Save enrichment result."""
        self.conn.execute("""
            INSERT INTO enriched
            (review_id, topic, subtopic, intent, sentiment, severity, entities, evidence_quote, needs_review, label_config, cache_source_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            review_id,
            labels["topic"],
            labels["subtopic"],
            labels["intent"],
            labels["sentiment"],
            labels["severity"],
            json.dumps(labels.get("entities", [])),
            labels["evidence_quote"],
            labels.get("needs_review", False),
            label_config,
            cache_source_id
        ])
        self.conn.commit()

    def log_api_call(self, request_id: str, role: str, model: str, review_ids: list,
                     input_tokens: int, output_tokens: int, outcome: str,
                     label_config: Optional[str] = None, phase: str = "initial"):
        """Log an API call for cost tracking."""
        self.conn.execute("""
            INSERT INTO api_calls
            (request_id, role, model, phase, review_ids, input_tokens, output_tokens, outcome, label_config)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            request_id,
            role,
            model,
            phase,
            json.dumps(review_ids),
            input_tokens,
            output_tokens,
            outcome,
            label_config
        ])
        self.conn.commit()

    def get_completed_ids(self, phase: Optional[str] = None) -> set[str]:
        """Get all enriched review IDs, optionally filtered by phase in the call log."""
        query = "SELECT review_id FROM enriched"
        rows = self.conn.execute(query).fetchall()
        return {r[0] for r in rows}

    def get_api_usage(self) -> dict:
        """Sum API calls by role and model."""
        result = self.conn.execute("""
            SELECT role, model, outcome,
                   COUNT(*) as calls,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output
            FROM api_calls
            WHERE outcome = 'succeeded'
            GROUP BY role, model, outcome
            ORDER BY role, model
        """).fetchall()

        usage = {}
        for role, model, outcome, calls, input_toks, output_toks in result:
            key = f"{role}:{model}"
            usage[key] = {
                "calls": calls,
                "input_tokens": input_toks or 0,
                "output_tokens": output_toks or 0,
            }
        return usage

    def close(self):
        """Close the database connection."""
        self.conn.close()
