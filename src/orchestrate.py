"""Main orchestrator: run stages in order."""
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

from src.state import StateStore
from src.stages import Enricher, Verifier, Grouper, Ranker, Recommender


class Pipeline:
    """Full pipeline orchestrator."""

    def __init__(self, db_path: str = "data/pipeline.db", config: Optional[dict] = None):
        self.store = StateStore(db_path)
        self.config = config or {}
        self.run_id = datetime.now().isoformat()
        self.run_log = []

    def enrich(self, batch_size: int = 50, max_batches: Optional[int] = None) -> dict:
        """Run enrichment stage."""
        enricher = Enricher(self.store, self.config.get("enricher", {}))

        batch_num = 0
        total_enriched = 0

        while True:
            if max_batches and batch_num >= max_batches:
                break

            batch = self.store.get_pending_reviews(limit=batch_size)
            if not batch:
                break

            results = enricher.run(batch)
            for result in results:
                if "error" not in result:
                    self.store.save_enrichment(result["k"], result, enricher.label_config)
                    total_enriched += 1

            batch_num += 1

        return {
            "stage": "enrich",
            "batches": batch_num,
            "enriched": total_enriched,
            "run_id": self.run_id,
        }

    def verify(self, sample_size: int = 10) -> dict:
        """Run verification stage on a sample."""
        verifier = Verifier(self.store, self.config.get("verifier", {}))

        # Get a sample of enriched reviews
        sample = self.store.conn.execute("""
            SELECT review_id, review_text
            FROM ingestion
            WHERE review_id IN (SELECT review_id FROM enriched)
            ORDER BY RANDOM()
            LIMIT ?
        """, [sample_size]).fetchall()

        batch = [{"k": r[0], "text": r[1]} for r in sample]
        results = verifier.run(batch)

        return {
            "stage": "verify",
            "sample_size": len(batch),
            "verified": len(results),
        }

    def rank(self) -> dict:
        """Run ranking stage."""
        ranker = Ranker(self.store, self.config.get("ranker", {}))
        result = ranker.run()
        return result

    def close(self):
        """Close database connection."""
        self.store.close()


if __name__ == "__main__":
    import sys

    db = sys.argv[1] if len(sys.argv) > 1 else "data/pipeline.db"
    pipe = Pipeline(db)

    # Run enrichment on first 5 reviews (for testing)
    result = pipe.enrich(batch_size=5, max_batches=1)
    print(json.dumps(result, indent=2))

    pipe.close()
