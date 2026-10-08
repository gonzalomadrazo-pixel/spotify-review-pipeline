"""Read-only dashboard API over the dashboard database (Postgres when deployed, SQLite locally).

Serves saved pipeline outputs only; it never calls a model. Local: uv run --group dashboard uvicorn
dashboard.api.index:app --port 8000. On Vercel this file is the Python serverless function for /api/*.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

LOCAL_DB = Path(__file__).resolve().parent.parent / "dashboard.db"
TOPICS = ("access", "usability", "playback", "downloads", "catalog", "billing", "support", "other")
INTENTS = ("cancellation", "complaint", "request", "praise", "unclear")

app = FastAPI(title="Spotify review analysis API", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def database_url() -> str:
    # Without DATABASE_URL the API reads the SQLite database file shipped next to it, opened read-only.
    url = os.environ.get("DATABASE_URL") or f"sqlite:///file:{LOCAL_DB.as_posix()}?mode=ro&uri=true"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@lru_cache(maxsize=1)
def engine():
    url = database_url()
    return create_engine(url, poolclass=NullPool) if url.startswith("postgresql") else create_engine(url)


def rows(sql: str, **params) -> list[dict]:
    with engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def one(sql: str, **params) -> dict | None:
    r = rows(sql, **params)
    return r[0] if r else None


def doc(name: str):
    r = one("SELECT json FROM evidence_docs WHERE name = :n", n=name)
    return json.loads(r["json"]) if r else None


def review_out(r: dict) -> dict:
    r = dict(r)
    r["entities"] = json.loads(r.pop("entities_json") or "[]")
    r["needs_review"] = None if r.get("needs_review") is None else bool(r["needs_review"])
    return r


@app.get("/api/health")
def health():
    n = one("SELECT COUNT(*) AS n FROM reviews")
    return {"ok": True, "database": database_url().split(":")[0], "reviews": n["n"] if n else 0}


@app.get("/api/overview")
def overview():
    run = one("SELECT * FROM run_info")
    if not run:
        raise HTTPException(503, "Database is empty: load a pipeline run with dashboard/load_db.py")
    summary = json.loads(run["summary_json"])
    status = {r["status"]: r["n"] for r in rows("SELECT status, COUNT(*) AS n FROM reviews GROUP BY status")}
    completed = "status = 'completed'"
    by_topic = rows(f"SELECT topic, COUNT(*) AS reviews, SUM(CASE WHEN intent IN ('complaint','cancellation') THEN 1 ELSE 0 END) "
                    f"AS complaints FROM reviews WHERE {completed} GROUP BY topic ORDER BY complaints DESC")
    by_intent = rows(f"SELECT intent, COUNT(*) AS n FROM reviews WHERE {completed} GROUP BY intent ORDER BY n DESC")
    by_severity = rows(f"SELECT severity, COUNT(*) AS n FROM reviews WHERE {completed} GROUP BY severity ORDER BY severity")
    agg = one(f"SELECT AVG(sentiment) AS mean_sentiment, SUM(needs_review) AS needs_review, "
              f"SUM(CASE WHEN cache_source_id IS NOT NULL THEN 1 ELSE 0 END) AS cache_reuse FROM reviews WHERE {completed}")
    quarantine = rows("SELECT reason, COUNT(*) AS n FROM reviews WHERE status = 'quarantined' GROUP BY reason")
    memo = one("SELECT status, model FROM memo")
    return {
        "run": {"run_id": run["run_id"], "generated_at": run["loaded_at"], "input_sha256": run["input_sha256"],
                "label_config": run["label_config"], "scope": json.loads(run["scope_json"] or "{}"),
                "spend_usd_actual": summary.get("spend_usd_actual"), "budget_usd": summary.get("budget_usd")},
        "status_counts": status, "total": sum(status.values()), "by_topic": by_topic, "by_intent": by_intent,
        "by_severity": by_severity, "mean_sentiment": agg["mean_sentiment"], "needs_review": agg["needs_review"],
        "exact_text_cache_reuse": agg["cache_reuse"], "quarantine_reasons": quarantine,
        "areas": rows("SELECT * FROM area_rollup ORDER BY complaint_count DESC"),
        "top_issues": rows("SELECT * FROM issues ORDER BY rank LIMIT 8"),
        "issue_count": one("SELECT COUNT(*) AS n FROM issues")["n"],
        "memo": memo, "verification": doc("verify_summary"), "golden": doc("golden_summary"),
    }


@app.get("/api/issues")
def issues():
    return rows("SELECT * FROM issues ORDER BY rank")


@app.get("/api/issues/{issue_id}")
def issue(issue_id: str, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)):
    item = one("SELECT * FROM issues WHERE issue_id = :i", i=issue_id)
    if not item:
        raise HTTPException(404, "Unknown issue")
    sev = rows("SELECT r.severity, COUNT(*) AS n FROM membership m JOIN reviews r ON r.review_id = m.review_id "
               "WHERE m.issue_id = :i GROUP BY r.severity ORDER BY r.severity", i=issue_id)
    members = rows("SELECT r.* FROM membership m JOIN reviews r ON r.review_id = m.review_id WHERE m.issue_id = :i "
                   "ORDER BY r.severity DESC, r.review_id LIMIT :lim OFFSET :off", i=issue_id, lim=page_size,
                   off=(page - 1) * page_size)
    monthly = rows("SELECT r.month, COUNT(*) AS n FROM membership m JOIN reviews r ON r.review_id = m.review_id "
                   "WHERE m.issue_id = :i GROUP BY r.month ORDER BY r.month", i=issue_id)
    claims = rows("SELECT * FROM claims WHERE issue_id = :i ORDER BY claim_id", i=issue_id)
    return {"issue": item, "severity_distribution": sev, "monthly": monthly, "claims": claims,
            "members": [review_out(m) for m in members], "page": page, "page_size": page_size,
            "member_count": item["complaint_count"]}


@app.get("/api/reviews")
def reviews(topic: str | None = None, intent: str | None = None, severity: int | None = Query(None, ge=1, le=5),
            issue_id: str | None = None, status: str | None = None, needs_review: bool | None = None,
            q: str | None = Query(None, max_length=200), page: int = Query(1, ge=1),
            page_size: int = Query(25, ge=1, le=100)):
    where, params = [], {}
    for col, val in (("topic", topic), ("intent", intent), ("severity", severity), ("issue_id", issue_id),
                     ("status", status)):
        if val is not None:
            where.append(f"{col} = :{col}")
            params[col] = val
    if needs_review is not None:
        where.append("needs_review = :nr")
        params["nr"] = int(needs_review)
    if q:
        where.append("LOWER(review_text) LIKE :q")
        params["q"] = f"%{q.lower()}%"
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = one(f"SELECT COUNT(*) AS n FROM reviews {clause}", **params)["n"]
    data = rows(f"SELECT * FROM reviews {clause} ORDER BY idx LIMIT :lim OFFSET :off", **params, lim=page_size,
                off=(page - 1) * page_size)
    return {"total": total, "page": page, "page_size": page_size, "items": [review_out(r) for r in data]}


@app.get("/api/reviews/{review_id}")
def review(review_id: str):
    r = one("SELECT * FROM reviews WHERE review_id = :i", i=review_id)
    if not r:
        raise HTTPException(404, "Unknown review")
    out = review_out(r)
    out["verification"] = one("SELECT * FROM verification WHERE review_id = :i", i=review_id)
    out["issue"] = one("SELECT issue_id, rank, title, priority_score FROM issues WHERE issue_id = :i",
                       i=r["issue_id"]) if r["issue_id"] else None
    if r["cache_source_id"]:
        out["cache_source"] = one("SELECT review_id, review_text FROM reviews WHERE review_id = :i", i=r["cache_source_id"])
    return out


@app.get("/api/recommendation")
def recommendation():
    memo = one("SELECT * FROM memo")
    if not memo:
        raise HTTPException(404, "No memo saved for this run")
    checks = json.loads(memo.pop("checks_json") or "{}")
    cited = (checks.get("check") or {}).get("cited_fact_ids") or []
    facts = rows("SELECT * FROM facts ORDER BY fact_id")
    return {"memo": memo, "checks": {"status": checks.get("status"), "check": checks.get("check"),
                                     "revisions": len(checks.get("attempts") or []),
                                     "evidence_review_ids": checks.get("evidence_review_ids"),
                                     "supplied_issue_ids": checks.get("supplied_issue_ids")},
            "facts": facts, "cited_fact_ids": cited, "claims": rows("SELECT * FROM claims ORDER BY claim_id")}


@app.get("/api/trends")
def trends():
    return rows("SELECT * FROM trend_monthly ORDER BY month, area")


@app.get("/api/evidence")
def evidence():
    run = one("SELECT summary_json FROM run_info")
    return {"run_summary": json.loads(run["summary_json"]) if run else None,
            **{d["name"]: json.loads(d["json"]) for d in rows("SELECT name, json FROM evidence_docs")},
            "verification_rows": rows("SELECT * FROM verification WHERE disagreement = 1 ORDER BY review_id LIMIT 200")}
