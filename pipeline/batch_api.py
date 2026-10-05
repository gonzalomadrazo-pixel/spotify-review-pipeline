"""Optional enrichment transport: Anthropic Message Batches API (50% price, asynchronous).

Each Message Batch request is still one bounded enrichment request (<= 50 reviews). Jobs are recorded
in SQLite before polling, so an interrupted process can resume by collecting the same jobs instead of
paying to resubmit them. Results go through exactly the same validation/commit code as the sync path;
invalid items are retried once (sync), then routed to the capped fallback or quarantined.
"""

from __future__ import annotations

import json
import re
import time

from .common import now_iso, usage_cost
from .enrich import EnrichDispatcher, Item, fallback_quota
from .llm import Attempt, Work, estimate_input_tokens


def _custom_id(run_id: str, invocation: int, n: int) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]", "-", run_id)[:40]
    return f"{base}-i{invocation}-{n:06d}"


def submit_jobs(ctx, client, disp: EnrichDispatcher, batches: list[list[Item]], requests_per_job: int) -> list[str]:
    submitted = []
    store = ctx.store
    seq0 = int(store.scalar("SELECT COUNT(*) FROM batch_jobs WHERE run_id=?", (ctx.run_id,)) or 0) * 100000
    for j in range(0, len(batches), requests_per_job):
        group = batches[j:j + requests_per_job]
        reqs, mapping, reserve = [], {}, 0.0
        for n, items in enumerate(group):
            cid = _custom_id(ctx.run_id, ctx.invocation, seq0 + j + n)
            work = Work(key=cid, payload=items, review_ids=[i.rep_id for i in items], tier="batch")
            req = disp.build(work)
            reserve += ctx.ledger.estimate(req.model, "batch", estimate_input_tokens(req.system, req.user), req.max_tokens)
            reqs.append((cid, req))
            mapping[cid] = [{"text_sha": i.text_sha, "rep_id": i.rep_id, "text": i.text, "needs_quote": i.needs_quote}
                            for i in items]
        if not ctx.ledger.can_reserve(reserve):
            ctx.log("budget_stop", role="enrich", transport="batch_api", spent=round(ctx.ledger.spent(), 6),
                    next_reservation=round(reserve, 6), budget=ctx.ledger.budget)
            return submitted
        if ctx.stop_requested():
            return submitted
        batch_id = client.submit_batch(reqs)
        with store.tx() as db:
            db.execute("INSERT INTO batch_jobs VALUES (?,?,?,?,?,?,?,?)",
                       (batch_id, ctx.run_id, ctx.invocation, now_iso(), "submitted", json.dumps(mapping), reserve, None))
        ctx.log("progress", stage="enrich", transport="batch_api", submitted_batch=batch_id, requests=len(reqs),
                reserved_usd=round(reserve, 4))
        submitted.append(batch_id)
    return submitted


def collect_job(ctx, client, disp: EnrichDispatcher, job) -> list[Work]:
    """Stream one ended job's results; log each request as a call and commit/validate its items."""
    mapping = json.loads(job["requests_json"])
    follow = []
    sub_inv = job["invocation"]
    phase = "initial" if sub_inv == 1 else "resume"
    for cid, attempt in client.batch_results(job["batch_id"]):
        items = [Item(x["text_sha"], x["rep_id"], x["text"], x["needs_quote"]) for x in mapping.get(cid, [])]
        if not items:
            continue
        request_id = f"{job['batch_id']}:{cid}"
        if ctx.store.scalar("SELECT 1 FROM calls WHERE request_id=?", (request_id,)):
            continue  # already collected before an interruption
        usage = attempt.usage
        cost = usage_cost(ctx.ledger.rates, disp.ecfg.model, "batch", usage)[0] if usage else 0.0
        with ctx.store.tx():
            ctx.store.insert_call({
                "request_id": request_id, "run_id": ctx.run_id, "invocation": sub_inv, "role": "enrich",
                "phase": phase, "model": disp.ecfg.model, "label_config": disp.ecfg.label_config, "tier": "batch",
                "review_ids": json.dumps([i.rep_id for i in items]), "n_items": len(items), "attempt": 1,
                "outcome": "succeeded" if attempt.ok else "failed", "error": attempt.error,
                "input_tokens": (usage or {}).get("input_tokens", 0),
                "cache_creation_input_tokens": (usage or {}).get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": (usage or {}).get("cache_read_input_tokens", 0),
                "output_tokens": (usage or {}).get("output_tokens", 0), "usage_available": int(usage is not None),
                "cost_usd": cost, "cost_basis": "actual_usage" if usage else "no_usage_error", "reserved_usd": 0.0,
                "started_at": job["created_at"], "ended_at": now_iso(), "duration_s": None,
                "stop_reason": attempt.stop_reason, "message_id": attempt.message_id, "batch_id": job["batch_id"],
                "seq": None, "provider_request_id": None,
            })
        work = Work(key=cid, payload=items, review_ids=[i.rep_id for i in items], tier="batch")
        if attempt.ok:
            follow.extend(disp.on_success(work, attempt, request_id))
        else:
            # Errored/expired batch requests are retried on the sync path (bounded like any transient error).
            follow.append(Work(key=f"{cid}-sync", payload=items, review_ids=work.review_ids, attempt_transient=1))
    with ctx.store.tx() as db:
        db.execute("UPDATE batch_jobs SET status='processed', ended_at=? WHERE batch_id=?", (now_iso(), job["batch_id"]))
    return follow


def run_enrich_batch_api(ctx, client, ecfg, fb_cfg, batches, requests_per_job: int = 500, poll_s: float = 30.0):
    disp = EnrichDispatcher(ctx, client, ecfg, fb_cfg, fallback_quota(ctx, ctx.cfg) if fb_cfg else 0)
    store = ctx.store
    open_jobs = store.q("SELECT * FROM batch_jobs WHERE run_id=? AND status NOT IN ('processed','canceled')", (ctx.run_id,))
    in_jobs = set()
    for job in open_jobs:
        for items in json.loads(job["requests_json"]).values():
            in_jobs.update(x["text_sha"] for x in items)
    batches = [[i for i in b if i.text_sha not in in_jobs] for b in batches]
    batches = [b for b in batches if b]
    if open_jobs:
        ctx.log("progress", stage="enrich", transport="batch_api", resuming_jobs=[j["batch_id"] for j in open_jobs])
    submit_jobs(ctx, client, disp, batches, requests_per_job)
    follow = []
    stop = None
    while True:
        jobs = store.q("SELECT * FROM batch_jobs WHERE run_id=? AND status NOT IN ('processed','canceled')", (ctx.run_id,))
        if not jobs:
            break
        for job in jobs:
            status, counts = client.retrieve_batch(job["batch_id"])
            if status == "ended":
                follow.extend(collect_job(ctx, client, disp, job))
                ctx.log("progress", stage="enrich", transport="batch_api", collected=job["batch_id"],
                        completed_records=ctx.completed_count())
            else:
                ctx.log("progress", stage="enrich", transport="batch_api", batch=job["batch_id"], status=status, **counts)
        if ctx.stop_requested():
            stop = ctx.stop_requested()
            break
        if ctx.deadline and time.time() > ctx.deadline:
            stop = "time_cap"
            break
        if store.q("SELECT 1 FROM batch_jobs WHERE run_id=? AND status NOT IN ('processed','canceled')", (ctx.run_id,)):
            time.sleep(poll_s)
    if follow and not stop:
        stop = disp.run(follow, max(1, ctx.workers))
    return stop or "completed", disp.stats


def attempt_from_batch_result(result) -> tuple[str, Attempt]:
    r = result.result
    if r.type == "succeeded":
        msg = r.message
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        u = msg.usage
        usage = {"input_tokens": u.input_tokens or 0, "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
                 "cache_read_input_tokens": u.cache_read_input_tokens or 0, "output_tokens": u.output_tokens or 0}
        return result.custom_id, Attempt(ok=True, text=text, stop_reason=msg.stop_reason, usage=usage, message_id=msg.id)
    err = getattr(r, "error", None)
    detail = getattr(getattr(err, "error", None), "type", None) or r.type
    return result.custom_id, Attempt(ok=False, error=f"batch_{r.type}:{detail}", error_kind="transient")
