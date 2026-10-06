"""Orchestrator: fixed stage order, saved handoffs between roles, run exports and the grading adapter.

Stage order (code decides what runs next; models only exercise judgment inside a stage):
  ingest (code) -> enrich (model + code validation) -> verify (model sample + code comparison)
  -> rank/membership (code) -> group naming (model, bounded packs) -> aggregates/facts (code)
  -> memo (model, code-checked claims) -> export (code)
Each stage reads the previous stage's saved artifacts in SQLite / runs/<run_id>/ and writes its own.
"""

from __future__ import annotations

import gzip
import json
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path

from .analysis import area_rollup, build_facts, check_claims, monthly_trend, period_shares
from .common import ROOT, now_iso, sha256_file, write_csv, write_json, write_jsonl
from .rank import RANK_FIELDS, write_rank_outputs

CONTRACT_KEYS = ("topic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review")


def input_sha(ctx):
    return ctx.store.scalar("SELECT input_sha FROM runs WHERE run_id=?", (ctx.run_id,))


def load_records(ctx) -> list[dict]:
    """All records of the run joined with their source row (exact strings preserved)."""
    rows = ctx.store.q(
        "SELECT r.*, s.source_sha256, s.review_timestamp, s.review_rating, s.app_version FROM records r "
        "JOIN rows s ON s.input_sha=? AND s.idx=r.idx WHERE r.run_id=? ORDER BY r.idx", (input_sha(ctx), ctx.run_id))
    out = []
    for r in rows:
        d = dict(r)
        if d["label_json"]:
            d.update(json.loads(d["label_json"]))
        out.append(d)
    return out


def contract_record(r: dict) -> dict:
    if r["status"] == "completed":
        rec = {"review_id": r["review_id"], "source_sha256": r["source_sha256"], "status": "completed"}
        rec.update({k: r[k] for k in CONTRACT_KEYS})
        rec["label_config"] = r["label_config"]
        if r["result_source"] in ("exact_text_reuse", "result_cache_duplicate") and r["cache_source_id"]:
            rec["cache_source_id"] = r["cache_source_id"]
        return rec
    if r["status"] == "quarantined":
        return {"review_id": r["review_id"], "source_sha256": r["source_sha256"], "status": "quarantined",
                "reason": r["reason"] or "unknown"}
    return {"review_id": r["review_id"], "source_sha256": r["source_sha256"], "status": r["status"]}


def call_rows(ctx, where="run_id=?", params=None):
    rows = ctx.store.q(f"SELECT * FROM calls WHERE {where} ORDER BY started_at, request_id", params or (ctx.run_id,))
    out = []
    for c in rows:
        d = dict(c)
        d["review_ids"] = json.loads(d["review_ids"] or "[]")
        if d["outcome"] == "dispatched":  # process ended before the call returned
            d["outcome"] = "failed"
            d["error"] = "unknown_outcome_process_terminated"
            d["unknown_charge"] = 1
        out.append(d)
    return out


def contract_call(c: dict, phase_override=None) -> dict:
    total_in = (c["input_tokens"] or 0) + (c["cache_creation_input_tokens"] or 0) + (c["cache_read_input_tokens"] or 0)
    return {"request_id": c["request_id"], "role": c["role"], "review_ids": c["review_ids"], "model": c["model"],
            "phase": phase_override or c["phase"], "outcome": c["outcome"], "label_config": c["label_config"],
            "input_tokens": int(total_in), "output_tokens": int(c["output_tokens"] or 0),
            "uncached_input_tokens": int(c["input_tokens"] or 0),
            "cache_creation_input_tokens": int(c["cache_creation_input_tokens"] or 0),
            "cache_read_input_tokens": int(c["cache_read_input_tokens"] or 0),
            "usage_available": bool(c["usage_available"]), "tier": c["tier"], "cost_usd": round(c["cost_usd"] or 0, 8),
            "cost_basis": c["cost_basis"], "attempt": c["attempt"], "error": c["error"], "run_id": c["run_id"],
            "invocation": c["invocation"], "started_at": c["started_at"], "ended_at": c["ended_at"],
            "duration_s": c["duration_s"], "provider_request_id": c["provider_request_id"],
            "message_id": c["message_id"], "batch_id": c["batch_id"], "artifact": c["artifact"]}


# ---------------------------------------------------------------- downstream stages


def coverage(records) -> dict:
    st = Counter(r["status"] for r in records)
    comp = [r for r in records if r["status"] == "completed"]
    return {"rows": len(records), "completed": st["completed"], "quarantined": st["quarantined"],
            "pending": st["pending"], "complaints": sum(r["intent"] in ("complaint", "cancellation") for r in comp),
            "cancellations": sum(r["intent"] == "cancellation" for r in comp),
            "quarantine_reasons": dict(Counter(r["reason"] for r in records if r["status"] == "quarantined"))}


def stage_rank(ctx) -> dict:
    ctx.log("stage_start", stage="rank")
    t0 = time.perf_counter()
    records = load_records(ctx)
    completed = [r for r in records if r["status"] == "completed"]
    ranking = write_rank_outputs(ctx.run_dir, completed)
    areas = area_rollup(completed)
    write_csv(ctx.run_dir / "area_rollup.csv", list(areas[0].keys()), areas)
    trend = monthly_trend(records, {r["review_id"]: r for r in completed})
    write_csv(ctx.run_dir / "trend_monthly.csv", list(trend[0].keys()), trend)
    write_csv(ctx.run_dir / "aggregates.csv", RANK_FIELDS + ["topic"], [{**r, "topic": r["issue_id"].split(".")[0]}
                                                                        for r in ranking])
    ctx.log("stage_end", stage="rank", issues=len(ranking), seconds=round(time.perf_counter() - t0, 3))
    return {"ranking": ranking, "areas": areas, "trend": trend, "records": records, "completed": completed}


def coverage_note(cov) -> str:
    return (f"Input rows: {cov['rows']}; completed: {cov['completed']}; quarantined: {cov['quarantined']} "
            f"(reasons: {cov['quarantine_reasons']}); pending: {cov['pending']}. Window May 2022 - November 2023; "
            "first and last calendar months are partial. Reviews are self-selected public Google Play reviews with "
            "no revenue, plan tier or confirmed churn data; labels come from a small model and have measured error. "
            "Numbers in this SCOPE block are context only; cite FACTS for any number you use.")


def stage_group_and_memo(ctx, client, ranked: dict, run_memo_stage: bool = True) -> dict:
    from .group import run_group
    from .memo import run_memo
    issues = run_group(ctx, client, ranked["completed"], ranked["ranking"])
    cov = coverage(ranked["records"])
    periods = period_shares(ranked["trend"])
    top_n = ctx.cfg["memo"]["top_issues"]
    facts = build_facts(ranked["ranking"], issues, ranked["areas"], periods, cov, top_n)
    errors = check_claims(facts, ranked["ranking"], ranked["areas"], periods, cov, top_n)
    write_json(ctx.run_dir / "facts.json", {"facts": facts, "periods": periods, "recompute_errors": errors})
    memo = run_memo(ctx, client, facts, issues, coverage_note(cov)) if run_memo_stage else None
    return {"issues": issues, "facts": facts, "memo": memo}


# ---------------------------------------------------------------- exports


def export_run(ctx) -> dict:
    """Write the run's inspectable handoffs: records, enriched, quarantine, calls, summary."""
    records = load_records(ctx)
    write_jsonl(ctx.run_dir / "records.jsonl", (contract_record(r) for r in records))
    rich_keys = ("review_id", "source_sha256", "status", "topic", "subtopic", "intent", "sentiment", "severity",
                 "entities", "evidence_quote", "needs_review", "review_reason", "quote_method", "rule_adjustments",
                 "label_config", "cache_source_id", "result_source", "source_request_id", "attempts",
                 "completed_invocation")
    write_jsonl(ctx.run_dir / "enriched.jsonl",
                ({k: r.get(k) for k in rich_keys} for r in records if r["status"] == "completed"))
    write_jsonl(ctx.run_dir / "quarantine.jsonl",
                ({"review_id": r["review_id"], "source_sha256": r["source_sha256"], "reason": r["reason"],
                  "attempts": r["attempts"]} for r in records if r["status"] == "quarantined"))
    calls = call_rows(ctx)
    write_jsonl(ctx.run_dir / "calls.jsonl", (contract_call(c) for c in calls))
    summary = run_summary(ctx, records, calls)
    write_json(ctx.run_dir / "run_summary.json", summary)
    return summary


def run_summary(ctx, records, calls) -> dict:
    by_role = defaultdict(lambda: Counter())
    for c in calls:
        k = f"{c['role']}|{c['model']}|{c['tier']}"
        by_role[k]["attempts"] += 1
        by_role[k]["succeeded"] += c["outcome"] == "succeeded"
        by_role[k]["failed"] += c["outcome"] == "failed"
        by_role[k]["retries"] += (c["attempt"] or 1) > 1
        by_role[k]["review_ids_sent"] += len(c["review_ids"])
        for f in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
            by_role[k][f] += c[f] or 0
        by_role[k]["cost_usd_micro"] += round((c["cost_usd"] or 0) * 1e6)
        by_role[k]["unknown_charge_calls"] += c.get("unknown_charge") or 0
    stages = []
    starts = {}
    for line in (ctx.run_dir / "run_log.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e["kind"] == "stage_start":
            starts[(e["invocation"], e["stage"])] = e["at"]
        elif e["kind"] == "stage_end":
            stages.append({"invocation": e["invocation"], "stage": e["stage"],
                           "started_at": starts.get((e["invocation"], e["stage"])), "ended_at": e["at"],
                           **{k: v for k, v in e.items() if k in ("seconds", "stop_reason", "status")}})
    inv = [dict(r) for r in ctx.store.q("SELECT * FROM invocations WHERE run_id=? ORDER BY n", (ctx.run_id,))]
    cov = coverage(records)
    src = Counter(r["result_source"] for r in records if r["status"] == "completed")
    return {
        "run_id": ctx.run_id, "generated_at": now_iso(),
        "input": {"path": ctx.store.scalar("SELECT input_path FROM runs WHERE run_id=?", (ctx.run_id,)),
                  "sha256": input_sha(ctx)},
        "config_file": ctx.cfg.get("_path"), "budget_usd": ctx.budget,
        "coverage": cov, "result_sources": dict(src),
        "needs_review": sum(bool(r.get("needs_review")) for r in records if r["status"] == "completed"),
        "spend_usd_actual": round(sum((c["cost_usd"] or 0) for c in calls), 6),
        "calls_by_role_model_tier": {k: {**v, "cost_usd": v.pop("cost_usd_micro") / 1e6} for k, v in by_role.items()},
        "invocations": inv, "stages": stages,
        "notes": "cost_usd is computed from provider-reported usage x config/rates.csv. Calls with "
                 "unknown_charge (timeouts) have no usage and must be reconciled with the provider dashboard.",
    }


def _rel(path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def export_grading(ctx, out_dir: Path, before: Path, after: Path, input_path: Path) -> dict:
    """Assemble the course's grading/ adapter folder for the final full-corpus run."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = load_records(ctx)
    write_json(out_dir / "run.json", {"version": "a5-audit-v1", "analysis_count": len(records),
                                      "analysis_sha256": sha256_file(input_path),
                                      "classification_input_fields": ["review_text"], "allow_multi_issue": False})
    shutil.copyfile(ctx.run_dir / "ingestion.json", out_dir / "ingestion.json")
    with gzip.open(out_dir / "records.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(contract_record(r), ensure_ascii=False) + "\n")
    for name in ("membership.csv", "ranking.csv", "claims.csv"):
        shutil.copyfile(ctx.run_dir / name, out_dir / name)
    # Calls: every attempt of this run, plus the successful enrichment calls from earlier runs in the same
    # state whose results this run reused under the identical label_config (phase relative to this run).
    used_configs = {r["label_config"] for r in records if r["status"] == "completed"}
    own = call_rows(ctx)
    source_reqs = {r["source_request_id"] for r in records if r["status"] == "completed" and r["source_request_id"]}
    others = [c for c in call_rows(ctx, "run_id!=? AND role='enrich' AND outcome='succeeded'", (ctx.run_id,))
              if c["label_config"] in used_configs and c["request_id"] in source_reqs]
    with gzip.open(out_dir / "calls.jsonl.gz", "wt", encoding="utf-8") as f:
        for c in others:
            f.write(json.dumps({**contract_call(c, "initial"), "source_run_phase": c["phase"]}, ensure_ascii=False) + "\n")
        for c in own:
            f.write(json.dumps(contract_call(c), ensure_ascii=False) + "\n")
    for src, name in ((before, "checkpoint_before.json"), (after, "checkpoint_after.json")):
        data = json.loads(Path(src).read_text())
        write_json(out_dir / name, {"completed_ids": data["completed_ids"], "run_id": data["run_id"],
                                    "invocation": data["invocation"], "saved_at": data["saved_at"],
                                    "reason": data["reason"], "source_file": _rel(src)})
    return {"records": len(records), "calls_own": len(own), "calls_reused_from_other_runs": len(others)}
