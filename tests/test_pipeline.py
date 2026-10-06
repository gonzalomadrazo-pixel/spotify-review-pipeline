"""Offline tests (no API key, no paid calls). Failures are injected into a deterministic fake model.

Run: uv run pytest -q
"""

import json
from pathlib import Path

import pytest

from pipeline.common import ROOT, load_config
from pipeline.context import RunContext
from pipeline.enrich import Item, run_enrich, validate_output
from pipeline.extract import extract_entities, resolve_quote
from pipeline.ingest import run_ingest
from pipeline.llm import Attempt
from pipeline.rank import compute_ranking, mean_string
from tests.fake_model import fake_client

PILOT = ROOT / "data" / "raw" / "cost_100.csv"
pytestmark = pytest.mark.skipif(not PILOT.exists(), reason="dataset not unpacked into data/raw/")


def make_ctx(tmp_path, run_id="t", budget=1.0, **limits):
    cfg = load_config()
    cfg["limits"].update({"backoff_base_s": 0.01, "backoff_max_s": 0.05, "requests_per_minute": 100000, **limits})
    ctx = RunContext(cfg, run_id, tmp_path / "state", tmp_path / "runs", budget_usd=budget, workers=1)
    run_ingest(ctx, PILOT)
    ctx.begin_invocation()
    return ctx


def statuses(ctx):
    return dict(ctx.store.q("SELECT status, COUNT(*) FROM records WHERE run_id=? GROUP BY status", (ctx.run_id,)))


def calls(ctx):
    return [dict(r) for r in ctx.store.q("SELECT * FROM calls WHERE run_id=? ORDER BY seq", (ctx.run_id,))]


def test_happy_path_accounts_for_every_id(tmp_path):
    ctx = make_ctx(tmp_path)
    out = run_enrich(ctx, fake_client())
    assert out["stop_reason"] == "completed"
    assert statuses(ctx) == {"completed": 100}
    assert all(len(json.loads(c["review_ids"])) <= 50 for c in calls(ctx))


def test_malformed_output_retried_once_then_split(tmp_path):
    ctx = make_ctx(tmp_path)

    def faults(req, n):
        if n == 1:  # first enrichment request returns broken JSON
            return Attempt(ok=True, text='{"items": [ {"k": 1, ', stop_reason="end_turn",
                           usage={"input_tokens": 10, "output_tokens": 5})
    out = run_enrich(ctx, fake_client(faults))
    cs = calls(ctx)
    assert statuses(ctx) == {"completed": 100}
    assert out["batch_errors"] == 1
    assert len(cs) == 4  # bad batch (50) + two retry halves (25+25) + second batch
    assert sorted(len(json.loads(c["review_ids"])) for c in cs) == [25, 25, 50, 50]
    assert sum(c["attempt"] == 2 for c in cs) == 2


def test_persistently_invalid_items_are_quarantined_with_reason(tmp_path):
    ctx = make_ctx(tmp_path)
    ctx.cfg["fallback"]["enabled"] = False

    def faults(req, n):
        return Attempt(ok=True, text='{"items": []}', stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1})
    run_enrich(ctx, fake_client(faults))
    st = statuses(ctx)
    assert st.get("completed", 0) == 0 and st["quarantined"] == 100
    reasons = {r[0] for r in ctx.store.q("SELECT reason FROM records WHERE run_id=?", (ctx.run_id,))}
    assert reasons == {"invalid_model_output:missing_key"}
    assert max(r[0] for r in ctx.store.q("SELECT attempts FROM records WHERE run_id=?", (ctx.run_id,))) == 2


def test_transient_errors_back_off_and_recover(tmp_path):
    ctx = make_ctx(tmp_path)

    def faults(req, n):
        if n in (1, 2):
            return Attempt(ok=False, error="529 overloaded", error_kind="transient")
    run_enrich(ctx, fake_client(faults))
    cs = calls(ctx)
    assert statuses(ctx) == {"completed": 100}
    assert [c["outcome"] for c in cs[:3]] == ["failed", "failed", "succeeded"]
    assert any(json.loads(l)["kind"] == "retry_scheduled" for l in (ctx.run_dir / "run_log.jsonl").read_text().splitlines())


def test_budget_cap_stops_admitting_work_and_saves_progress(tmp_path):
    ctx = make_ctx(tmp_path, budget=0.03)  # room for one worst-case reservation (~$0.024), not two
    out = run_enrich(ctx, fake_client())
    assert out["stop_reason"] == "budget_cap"
    st = statuses(ctx)
    assert st["completed"] > 0 and st["pending"] > 0
    assert ctx.ledger.spent() <= 0.03
    assert any(json.loads(l)["kind"] == "budget_stop" for l in (ctx.run_dir / "run_log.jsonl").read_text().splitlines())


def test_provider_spend_limit_stops_immediately(tmp_path):
    ctx = make_ctx(tmp_path)

    def faults(req, n):
        return Attempt(ok=False, error="429 spend limit", error_kind="budget")
    out = run_enrich(ctx, fake_client(faults))
    assert out["stop_reason"] == "provider_spend_limit"
    assert len(calls(ctx)) == 1


def test_resume_makes_no_calls_for_completed_ids(tmp_path):
    ctx = make_ctx(tmp_path)
    run_enrich(ctx, fake_client(), limit_requests=1)
    before = {r[0] for r in ctx.store.q("SELECT review_id FROM records WHERE run_id=? AND status='completed'", (ctx.run_id,))}
    ctx.end_invocation("interrupted_test")
    ctx.begin_invocation()
    run_enrich(ctx, fake_client())
    resumed = [c for c in calls(ctx) if c["phase"] == "resume"]
    assert resumed and not before & {i for c in resumed for i in json.loads(c["review_ids"])}
    assert statuses(ctx) == {"completed": 100}


def test_validator_rejects_foreign_and_duplicate_keys():
    items = [Item("a", "id1", "x", False), Item("b", "id2", "y", False)]
    good = {"q": "", "sub": "other.general", "intent": "praise", "sev": 1, "sent": 1, "review": False, "why": "none"}
    text = json.dumps({"items": [{"k": 1, **good}, {"k": 1, **good}, {"k": 7, **good}]})
    valid, problems, err = validate_output(text, items, "end_turn")
    assert err is None and valid == {} and problems == {1: "duplicate_key", 2: "missing_key"}
    assert validate_output("{}", items, "max_tokens")[2] == "max_tokens_truncated"


def test_quotes_are_always_exact_substrings():
    text = "I love the playlists.\nBut the app  keeps CRASHING when I open it, every single time!"
    for proposed in ("the app keeps crashing when I open it", "“But the app keeps CRASHING”", "totally invented", ""):
        q, _ = resolve_quote(text, proposed, True)
        assert q and q in text
    assert resolve_quote("  Good  ", "", False) == ("Good", "full_text_short_review")


def test_entities_are_surface_substrings():
    text = "Too many ADS and Premium needed to Shuffle my playlist"
    ents = extract_entities(text)
    assert ents and all(e in text for e in ents)


def test_ranking_matches_contract_arithmetic():
    sev = {"a": 4, "b": 2, "c": 3, "d": 1}
    mem = [{"issue_id": "y.z", "review_id": "b"}, {"issue_id": "y.z", "review_id": "c"},
           {"issue_id": "x.z", "review_id": "a"}, {"issue_id": "a.a", "review_id": "d"}]
    rows = compute_ranking(sev, mem)
    assert [(r["rank"], r["issue_id"], r["priority_score"]) for r in rows] == [(1, "y.z", 5), (2, "x.z", 4), (3, "a.a", 1)]
    assert mean_string(2, 3) == "0.666667" and mean_string(1, 8) == "0.125000"


def test_batch_api_transport_resumes_submitted_jobs_without_resubmitting(tmp_path):
    from pipeline import batch_api
    from tests.fake_model import FakeBatchClient
    ctx = make_ctx(tmp_path)
    client = FakeBatchClient(polls_until_end=3)
    ctx.stop.reason = None
    # First invocation: submit, then "interrupt" before the job ends.
    orig_sleep = batch_api.time.sleep
    batch_api.time.sleep = lambda s: setattr(ctx.stop, "reason", "interrupted_test")
    try:
        out1 = run_enrich(ctx, client, mode="batch")
    finally:
        batch_api.time.sleep = orig_sleep
    assert out1["stop_reason"] == "interrupted_test" and len(client.jobs) == 1
    assert statuses(ctx) == {"pending": 100}
    reserved = ctx.ledger.external_reserved()
    assert reserved > 0  # submitted job keeps its spend reservation
    ctx.end_invocation("interrupted_test")
    ctx.stop.reason = None
    ctx.begin_invocation()
    out2 = run_enrich(ctx, client, mode="batch")
    assert out2["stop_reason"] == "completed" and len(client.jobs) == 1  # collected, not resubmitted
    assert statuses(ctx) == {"completed": 100}
    cs = calls(ctx)
    assert all(c["tier"] == "batch" and c["phase"] == "initial" for c in cs) and ctx.ledger.external_reserved() == 0
