"""Stage 1 - Prepare (code only, no model).

Reads every CSV row with the same strict parser as the course checker, preserves all six source
strings exactly, computes the contract's source row hash, profiles quality, finds exact duplicate
texts, and seeds per-run record statuses: empty text -> quarantined, everything else -> pending.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .common import FIELDS, ROOT, now_iso, sha256_file, sha256_text, write_json
from .vendor import check_submission as checker


def load_rows(store, input_path: Path) -> tuple[str, int]:
    input_path = Path(input_path)
    input_sha = sha256_file(input_path)
    existing = store.scalar("SELECT rows FROM inputs WHERE input_sha=?", (input_sha,))
    if existing is not None:
        return input_sha, int(existing)
    batch, n = [], 0
    with store.tx() as db:
        for idx, row in enumerate(checker.csv_rows(input_path)):
            missing = [k for k in FIELDS if k not in row]
            if missing:
                raise SystemExit(f"Input CSV is missing required columns: {missing}")
            batch.append((input_sha, idx, row["review_id"], checker.row_sha(row), sha256_text(row["review_text"]),
                          row["review_text"], row["review_rating"], row["review_likes"], row["app_version"],
                          row["review_timestamp"]))
            n += 1
            if len(batch) >= 20000:
                db.executemany("INSERT INTO rows VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            db.executemany("INSERT INTO rows VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
        db.execute("INSERT INTO inputs VALUES (?,?,?,?)", (input_sha, str(input_path), n, now_iso()))
    return input_sha, n


def seed_records(ctx, input_sha: str) -> dict:
    """Create one record per unique source ID for this run (idempotent across resumes)."""
    store = ctx.store
    have = store.scalar("SELECT COUNT(*) FROM records WHERE run_id=?", (ctx.run_id,))
    if have:
        return {"seeded": 0, "existing": have}
    seen, dup_rows, rows = set(), [], []
    for r in store.q("SELECT idx, review_id, text_sha, review_text FROM rows WHERE input_sha=? ORDER BY idx", (input_sha,)):
        if r["review_id"] in seen:
            dup_rows.append(r["idx"])
            continue
        seen.add(r["review_id"])
        if not r["review_text"].strip():
            rows.append((ctx.run_id, r["review_id"], r["idx"], r["text_sha"], "quarantined", "empty_review_text", now_iso()))
        else:
            rows.append((ctx.run_id, r["review_id"], r["idx"], r["text_sha"], "pending", None, now_iso()))
    with store.tx() as db:
        db.executemany("INSERT INTO records(run_id, review_id, idx, text_sha, status, reason, updated_at) "
                       "VALUES (?,?,?,?,?,?,?)", rows)
    if dup_rows:
        ctx.log("duplicate_review_id_rows_excluded", count=len(dup_rows), first_rows=dup_rows[:20])
    return {"seeded": len(rows), "duplicate_id_rows_excluded": len(dup_rows)}


def ingestion_report(store, input_path: Path, input_sha: str) -> dict:
    """Deterministic full-file profile: the course helper's exact profile plus extra quality stats."""
    official = checker.profile(input_path)  # identical format to grading/ingestion.json
    rows = store.q("SELECT review_id, review_text, review_rating, app_version, review_timestamp, text_sha "
                   "FROM rows WHERE input_sha=? ORDER BY idx", (input_sha,))
    texts = Counter()
    empty_ids, invalid_rating, id_counts = [], 0, Counter()
    lengths = []
    for r in rows:
        id_counts[r["review_id"]] += 1
        if not r["review_text"].strip():
            empty_ids.append(r["review_id"])
        else:
            texts[r["review_text"]] += 1
        if r["review_rating"] not in {"1", "2", "3", "4", "5"}:
            invalid_rating += 1
    for t in texts:
        lengths.append(len(t))
    lengths.sort()

    def q(p):
        return lengths[int(p * (len(lengths) - 1))] if lengths else None

    nonempty = sum(texts.values())
    dup_groups = [(t, c) for t, c in texts.items() if c > 1]
    manifest_match = None
    course_manifest = ROOT / "data" / "raw" / "manifest.json"
    if course_manifest.exists():
        m = json.loads(course_manifest.read_text())
        f = m.get("files", {}).get(Path(input_path).name)
        if f:
            manifest_match = {"file": Path(input_path).name, "expected_sha256": f["sha256"],
                              "expected_bytes": f["bytes"], "sha256_match": f["sha256"] == official["file_sha256"],
                              "bytes_match": f["bytes"] == Path(input_path).stat().st_size}
    return {
        "generated_at": now_iso(),
        "input_path": str(input_path),
        "input_bytes": Path(input_path).stat().st_size,
        "official_profile": official,
        "course_manifest_check": manifest_match,
        "accounting": {
            "rows": len(rows),
            "unique_review_ids": len(id_counts),
            "duplicate_review_id_rows": sum(c - 1 for c in id_counts.values() if c > 1),
            "empty_review_text": len(empty_ids),
            "empty_review_text_ids": empty_ids,
            "nonempty_to_classify": nonempty,
            "distinct_nonempty_texts": len(texts),
            "rows_in_duplicate_text_groups": sum(c for _, c in dup_groups),
            "duplicate_text_groups": len(dup_groups),
            "reusable_rows_if_exact_text_cache": nonempty - len(texts),
            "invalid_rating_rows": invalid_rating,
            "missing_app_version": official["counts"]["missing_app_version"],
        },
        "text_length_chars_distinct": {"p10": q(.1), "p25": q(.25), "p50": q(.5), "p75": q(.75), "p90": q(.9),
                                       "p99": q(.99), "max": lengths[-1] if lengths else None,
                                       "total_chars": sum(lengths)},
        "top_duplicate_texts": [{"text": t[:80], "rows": c} for t, c in sorted(dup_groups, key=lambda x: -x[1])[:20]],
        "notes": [
            "Exclusion policy: no rows are dropped. Empty/whitespace-only texts are quarantined with reason "
            "empty_review_text; missing app_version does not block classification.",
            "review_timestamp has no timezone in the source; months are taken from the first 7 characters.",
            "Exact-duplicate texts are classified once per label_config; every original ID keeps its own record "
            "with cache_source_id provenance and is counted separately in aggregates.",
        ],
    }


def run_ingest(ctx, input_path: Path) -> dict:
    ctx.log("stage_start", stage="ingest", input=str(input_path))
    input_sha, n = load_rows(ctx.store, input_path)
    existing = ctx.store.one("SELECT input_sha FROM runs WHERE run_id=?", (ctx.run_id,))
    if existing and existing["input_sha"] != input_sha:
        raise SystemExit(f"Run {ctx.run_id} was created for a different input file; choose a new --run-id.")
    if not existing:
        with ctx.store.tx() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?)",
                       (ctx.run_id, input_sha, str(input_path), now_iso(), json.dumps(ctx.cfg, sort_keys=True)))
    seeded = seed_records(ctx, input_sha)
    report_path = ctx.run_dir / "ingestion_report.json"
    if not report_path.exists():
        report = ingestion_report(ctx.store, input_path, input_sha)
        write_json(report_path, report)
        write_json(ctx.run_dir / "ingestion.json", report["official_profile"])
    ctx.log("stage_end", stage="ingest", rows=n, **seeded)
    return {"input_sha": input_sha, "rows": n}
