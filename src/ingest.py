"""Ingestion: read CSV, create database, prepare for classification."""
import json
from pathlib import Path
from typing import Optional

from src.state import StateStore


def ingest_full(csv_path: str, db_path: str = "data/pipeline.db") -> dict:
    """Ingest the full CSV, return profile and readiness report."""
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    store = StateStore(db_path)
    profile = store.ingest_csv(str(csv_path))
    store.close()

    return {
        "csv_path": str(csv_path),
        "db_path": db_path,
        "ingestion": profile,
        "nonempty_to_classify": profile["total_rows"] - profile["empty_texts"],
        "ready_for_enrichment": profile["total_rows"] - profile["empty_texts"],
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.ingest <csv_path> [db_path]")
        sys.exit(1)

    db = sys.argv[2] if len(sys.argv) > 2 else "data/pipeline.db"
    result = ingest_full(sys.argv[1], db)
    print(json.dumps(result, indent=2))
