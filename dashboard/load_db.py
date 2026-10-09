"""Load one pipeline run's saved outputs into the dashboard database (no model calls).

    uv run --group dashboard python dashboard/load_db.py --run-id final100k --input data/subset_100k.csv
    DATABASE_URL=postgresql+psycopg://... uv run --group dashboard python dashboard/load_db.py ...

Reads runs/<run_id>/ (records, enriched labels, ranking, membership, issues, facts, memo, claims, verifier
results, run summary) plus the input CSV for the original review text, then replaces every dashboard table.
The default target is the local SQLite file dashboard/dashboard.db.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.common import read_jsonl  # noqa: E402
from pipeline.vendor import check_submission as checker  # noqa: E402

TABLES = ("run_info", "reviews", "issues", "membership", "area_rollup", "trend_monthly", "facts", "claims", "memo",
          "verification", "evidence_docs")
AREAS = ("access", "usability", "playback", "billing_support", "downloads", "catalog", "other")


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


ROLE_CODE = {"enrich": 0, "verify": 1, "group": 2, "memo": 3}


def telemetry(run_dir: Path, summary: dict) -> dict | None:
    """Compact flight-recorder data: one row per model call plus invocations, stages and cache statistics."""
    path = run_dir / "calls.jsonl"
    if not path.exists():
        return None
    from datetime import datetime
    ts = lambda s: round(datetime.fromisoformat(s).timestamp(), 1) if s else None  # noqa: E731
    calls = []
    for c in read_jsonl(path):
        ids = c.get("review_ids") or []
        calls.append([ts(c.get("started_at")), round(c.get("duration_s") or 0, 1), len(ids) if isinstance(ids, list) else 0,
                      ROLE_CODE.get(c.get("role"), 9), c.get("invocation"), 1 if c.get("outcome") == "succeeded" else 0,
                      c.get("output_tokens") or 0, c.get("input_tokens") or 0])
    events = [json.loads(line) for line in (run_dir / "run_log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()] \
        if (run_dir / "run_log.jsonl").exists() else []
    progress = next((e for e in events if e.get("kind") == "progress"), {})
    return {
        "columns": ["start_epoch_s", "duration_s", "items", "role", "invocation", "ok", "output_tokens", "input_tokens"],
        "roles": list(ROLE_CODE), "calls": sorted(calls, key=lambda r: r[0] or 0),
        "invocations": [{k: i.get(k) for k in ("n", "phase", "stop_reason", "completed_before", "completed_after")}
                        | {"start": ts(i.get("started_at")), "end": ts(i.get("ended_at"))} for i in summary.get("invocations", [])],
        "stages": [{"stage": s.get("stage"), "invocation": s.get("invocation"), "start": ts(s.get("started_at")),
                    "end": ts(s.get("ended_at"))} for s in summary.get("stages", [])],
        "progress": {k: progress.get(k) for k in ("pending_records", "pending_distinct_texts", "result_cache_hits",
                                                   "result_cache_hit_records", "requests_planned")},
        "invalid_outputs": sum(1 for e in events if e.get("kind") == "invalid_output"),
        "result_sources": summary.get("result_sources", {}),
    }


def golden_detail() -> dict | None:
    """Confusion tables for the human golden set (rows = human label, columns = model label)."""
    out = {}
    for field in ("topic", "intent"):
        rows_ = read_csv(ROOT / "evals" / "golden" / f"confusion_{field}.csv")
        if rows_:
            first = list(rows_[0].keys())[0]
            out[field] = {"labels": [k for k in rows_[0] if k != first],
                          "rows": [{"human": r[first], "counts": [int(r[k]) for k in rows_[0] if k != first]} for r in rows_]}
    return out or None


def injection_detail() -> list[dict] | None:
    """Red-team cases with the model's label and the code-side injection screen (recomputed here, deterministic)."""
    from pipeline.extract import looks_like_injection
    res = read_json(ROOT / "evals" / "injection" / "results.json")
    texts = {r["review_id"]: r["review_text"] for r in read_csv(ROOT / "evals" / "injection" / "injection_cases.csv")}
    if not res:
        return None
    return [{"id": r["review_id"], "purpose": r.get("purpose"), "text": texts.get(r["review_id"], ""),
             "expected": r.get("expected"), "predicted": {k: r["predicted"].get(k) for k in ("topic", "intent", "severity")},
             "label_correct": bool(r.get("pass")), "screen_flagged": looks_like_injection(texts.get(r["review_id"], "")),
             "control": r["review_id"].startswith("syn-ctl")} for r in res.get("results", [])]


def build_rows(run_dir: Path, input_csv: Path) -> dict[str, list[dict]]:
    src = {r["review_id"]: (i, r) for i, r in enumerate(checker.csv_rows(input_csv))}
    final = {r["review_id"]: r for r in read_jsonl(run_dir / "records.jsonl")}
    rich = {r["review_id"]: r for r in read_jsonl(run_dir / "enriched.jsonl")}
    membership = read_csv(run_dir / "membership.csv")
    issue_of = {m["review_id"]: m["issue_id"] for m in membership}
    missing = set(final) - set(src)
    if missing:
        raise SystemExit(f"{len(missing)} run records are not in {input_csv}; pass the run's own input CSV.")

    reviews = []
    for rid, rec in final.items():
        idx, s = src[rid]
        e = rich.get(rid, {})
        reviews.append({
            "review_id": rid, "idx": idx, "review_text": s["review_text"], "review_rating": s["review_rating"],
            "review_likes": s["review_likes"], "app_version": s["app_version"],
            "review_timestamp": s["review_timestamp"], "month": s["review_timestamp"][:7],
            "status": rec["status"], "reason": rec.get("reason"), "topic": e.get("topic"), "subtopic": e.get("subtopic"),
            "intent": e.get("intent"), "sentiment": e.get("sentiment"), "severity": e.get("severity"),
            "evidence_quote": e.get("evidence_quote"),
            "needs_review": None if not e else int(bool(e.get("needs_review"))), "review_reason": e.get("review_reason"),
            "entities_json": json.dumps(e.get("entities") or [], ensure_ascii=False), "label_config": e.get("label_config"),
            "cache_source_id": e.get("cache_source_id"), "result_source": e.get("result_source"),
            "source_request_id": e.get("source_request_id"), "issue_id": issue_of.get(rid)})

    by_id = {r["review_id"]: r for r in reviews}
    named = {i["issue_id"]: i for i in (read_json(run_dir / "issues.json", {}) or {}).get("issues", [])}
    stats = {}
    for m in membership:
        r = by_id[m["review_id"]]
        c = stats.setdefault(m["issue_id"], Counter())
        c["cancellation_count"] += r["intent"] == "cancellation"
        c["severe_count"] += (r["severity"] or 0) >= 4
        c["needs_review_count"] += bool(r["needs_review"])
    issues = []
    for row in read_csv(run_dir / "ranking.csv"):
        iid, n = row["issue_id"], named.get(row["issue_id"], {})
        c = stats.get(iid, Counter())
        issues.append({"issue_id": iid, "rank": int(row["rank"]), "topic": iid.split(".")[0],
                       "title": n.get("title") or iid, "summary": n.get("summary") or n.get("definition"),
                       "coherence": n.get("coherence"), "complaint_count": int(row["complaint_count"]),
                       "severity_sum": int(row["severity_sum"]), "mean_severity": row["mean_severity"],
                       "priority_score": int(row["priority_score"]), "cancellation_count": c["cancellation_count"],
                       "severe_count": c["severe_count"], "needs_review_count": c["needs_review_count"]})

    areas = [{"area": a["area"], "complaint_count": int(a["complaint_count"] or 0),
              "severity_sum": int(a["severity_sum"] or 0), "mean_severity": a["mean_severity"],
              "share_of_complaints": a["share_of_complaints"], "severe_count": int(a["severe_count_sev4plus"] or 0),
              "cancellation_count": int(a["cancellation_count"] or 0),
              "total_complaints": int(a["total_complaints_all_areas"] or 0)}
             for a in read_csv(run_dir / "area_rollup.csv")]
    trend = []
    for t in read_csv(run_dir / "trend_monthly.csv"):
        trend.append({"month": t["month"], "area": "all", "reviews": int(t["reviews"]), "complaints": int(t["complaints"])})
        trend.extend({"month": t["month"], "area": a, "reviews": int(t["reviews"]), "complaints": int(t[a])}
                     for a in AREAS if a in t)

    facts = (read_json(run_dir / "facts.json", {}) or {}).get("facts", [])
    claims = read_csv(run_dir / "claims.csv")
    checks = read_json(run_dir / "memo_checks.json", {}) or {}
    memo_md = (run_dir / "memo.md").read_text(encoding="utf-8") if (run_dir / "memo.md").exists() else ""
    summary = read_json(run_dir / "run_summary.json", {}) or {}
    memo_call = next((c for c in reversed(list(read_jsonl(run_dir / "calls.jsonl")))
                      if c["role"] == "memo" and c["outcome"] == "succeeded"), {}) if (run_dir / "calls.jsonl").exists() else {}
    memo = [{"id": 1, "markdown": memo_md, "status": checks.get("status"), "model": memo_call.get("model"),
             "label_config": memo_call.get("label_config"), "checks_json": json.dumps(checks, ensure_ascii=False)}] \
        if memo_md else []

    verification = []
    vpath = run_dir / "verify" / "verifier_predictions.jsonl"
    if vpath.exists():
        for v in read_jsonl(vpath):
            if "verify_topic" in v:
                verification.append({"review_id": v["review_id"], "enrich_topic": v["enrich_topic"],
                                     "verify_topic": v["verify_topic"], "enrich_intent": v["enrich_intent"],
                                     "verify_intent": v["verify_intent"], "enrich_severity": v["enrich_severity"],
                                     "verify_severity": v["verify_severity"], "disagreement": int(v["disagreement"])})

    docs = {"verify_summary": run_dir / "verify" / "verify_summary.json",
            "ingestion_report": run_dir / "ingestion_report.json",
            "golden_summary": ROOT / "evals" / "golden" / "golden_summary.json",
            "injection_results": ROOT / "evals" / "injection" / "results.json",
            "planted_errors": ROOT / "evals" / "planted_errors.json",
            "cost_replay": ROOT / "cost" / "replay_result.json"}
    evidence = []
    for name, path in docs.items():
        doc = read_json(path)
        if doc is not None:
            if name == "ingestion_report":
                doc = {k: doc[k] for k in ("scope", "official_profile", "accounting", "text_length_chars_distinct",
                                           "notes") if k in doc}
            if name == "cost_replay":
                doc = {"measured": doc.get("measured"), "projections": doc.get("projections")}
            evidence.append({"name": name, "json": json.dumps(doc, ensure_ascii=False, default=str)})
    for name, doc in (("telemetry", telemetry(run_dir, summary)), ("golden_detail", golden_detail()),
                      ("injection_detail", injection_detail())):
        if doc is not None:
            evidence.append({"name": name, "json": json.dumps(doc, ensure_ascii=False, default=str, separators=(",", ":"))})

    label_configs = Counter(r["label_config"] for r in reviews if r["label_config"])
    run_info = [{"run_id": summary.get("run_id", run_dir.name), "loaded_at": summary.get("generated_at", ""),
                 "input_path": str(input_csv), "input_sha256": (summary.get("input") or {}).get("sha256"),
                 "label_config": label_configs.most_common(1)[0][0] if label_configs else None,
                 "summary_json": json.dumps(summary, ensure_ascii=False, default=str),
                 "scope_json": json.dumps((read_json(run_dir / "ingestion_report.json", {}) or {}).get("scope", {}))}]
    return {"run_info": run_info, "reviews": reviews, "issues": issues,
            "membership": [{"issue_id": m["issue_id"], "review_id": m["review_id"]} for m in membership],
            "area_rollup": areas, "trend_monthly": trend,
            "facts": [{k: f.get(k) for k in ("fact_id", "scope", "subject", "metric", "value", "display", "formula")}
                      for f in facts],
            "claims": claims, "memo": memo, "verification": verification, "evidence_docs": evidence}


def load(database_url: str, rows: dict[str, list[dict]]) -> dict:
    from sqlalchemy import create_engine, text
    engine = create_engine(database_url)
    ddl = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
    code = "\n".join(l for l in ddl.splitlines() if not l.strip().startswith("--"))
    statements = [s.strip() for s in code.split(";") if s.strip()]
    with engine.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        for stmt in statements:
            conn.execute(text(stmt))
        counts = {}
        for table in TABLES:
            data = rows.get(table) or []
            if data:
                cols = list(data[0].keys())
                sql = text(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})")
                for i in range(0, len(data), 5000):
                    conn.execute(sql, data[i:i + 5000])
            counts[table] = len(data)
    return counts


def main():
    import os
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", required=True)
    p.add_argument("--input", required=True, help="the run's input CSV (for original review text)")
    p.add_argument("--runs-dir", default=str(ROOT / "runs"))
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL")
                   or f"sqlite:///{(Path(__file__).parent / 'dashboard.db').as_posix()}")
    args = p.parse_args()
    run_dir = Path(args.runs_dir) / args.run_id
    if not any((run_dir / n).exists() for n in ("records.jsonl", "records.jsonl.gz")):
        raise SystemExit(f"No saved run at {run_dir}")
    counts = load(args.database_url, build_rows(run_dir, Path(args.input)))
    target = args.database_url.split("@")[-1] if "@" in args.database_url else args.database_url
    print(json.dumps({"database": target, "rows": counts}, indent=2))


if __name__ == "__main__":
    main()
