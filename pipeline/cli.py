"""Command line entry point: `uv run python -m pipeline <command> ...`

Offline commands (no API key, no model calls): status, make-subset, rank, rerank, export-run,
export-grading, eval-golden, injection-check, planted-errors.
Model commands (explicit): run, smoke. Each role calls the provider set in config/pipeline.json; the
default is a local Ollama model (no API charge). Roles switched to "anthropic" need ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import ROOT, load_config, write_csv, write_json

STAGES = ("ingest", "enrich", "verify", "rank", "group", "memo", "export")


def make_ctx(args, command):
    from .context import RunContext
    cfg = load_config(args.config)
    return RunContext(cfg, args.run_id, Path(args.state_dir), Path(args.runs_dir), budget_usd=args.budget,
                      workers=args.workers, max_minutes=args.max_minutes, command=command)


def get_client(args):
    if getattr(args, "fake_model", False):
        from tests.fake_model import fake_client
        return fake_client()
    from .llm import RoutingClient
    return RoutingClient(load_config(args.config))


def cmd_run(args):
    """Run the stages in order on an arbitrary input CSV. Safe to re-run: completed work is reused."""
    from .enrich import run_enrich
    from .ingest import run_ingest
    from .orchestrator import export_run, stage_group_and_memo, stage_rank
    from .verify import run_verify
    stages = args.stages.split(",") if args.stages else list(STAGES)
    ctx = make_ctx(args, " ".join(sys.argv))
    client = get_client(args) if set(stages) & {"enrich", "verify", "group", "memo"} else None
    run_ingest(ctx, Path(args.input), Path(args.full_input) if args.full_input else None)
    ctx.begin_invocation()
    stop = "completed"
    try:
        if "enrich" in stages:
            res = run_enrich(ctx, client, mode=args.mode, retry_quarantined=args.retry_quarantined,
                             limit_requests=args.limit_requests)
            stop = res["stop_reason"]
        pending = ctx.store.scalar("SELECT COUNT(*) FROM records WHERE run_id=? AND status='pending'", (ctx.run_id,))
        if stop != "completed" or pending:
            ctx.log("stage_skipped", stage="downstream", reason=f"enrichment incomplete (stop={stop}, pending={pending}); "
                    "rerun the same command to resume")
        else:
            if "verify" in stages:
                run_verify(ctx, client)
            if "rank" in stages or "group" in stages or "memo" in stages:
                ranked = stage_rank(ctx)
                if "group" in stages:
                    stage_group_and_memo(ctx, client, ranked, run_memo_stage="memo" in stages)
    finally:
        ctx.end_invocation(stop)
        summary = export_run(ctx)
    print(json.dumps({"run_id": ctx.run_id, "stop_reason": stop, "coverage": summary["coverage"],
                      "spend_usd_actual": summary["spend_usd_actual"]}, indent=2))


def cmd_make_subset(args):
    from .subset import build_subset
    m = build_subset(Path(args.full), Path(args.out), args.n)
    print(json.dumps({"subset": m["subset"], "nested_course_files": m["nested_course_files"]}, indent=2))


def cmd_status(args):
    from .store import Store
    store = Store(Path(args.state_dir))
    for r in store.q("SELECT run_id, input_path, created_at FROM runs ORDER BY created_at"):
        counts = dict(store.q("SELECT status, COUNT(*) FROM records WHERE run_id=? GROUP BY status", (r["run_id"],)))
        spend = store.run_spend(r["run_id"])
        calls = store.scalar("SELECT COUNT(*) FROM calls WHERE run_id=?", (r["run_id"],))
        print(f"{r['run_id']:<28} {Path(r['input_path']).name:<34} {json.dumps(counts)} calls={calls} spend=${spend:.4f}")


def cmd_rank(args):
    from .orchestrator import stage_rank
    ctx = make_ctx(args, "rank")
    ctx.invocation, ctx.phase = 0, "offline"
    out = stage_rank(ctx)
    print(f"ranking.csv written with {len(out['ranking'])} issues (no model calls)")


def cmd_rerank(args):
    """Regenerate ranking from saved records + membership only; compare to the saved ranking.csv."""
    import csv
    from .rank import RANK_FIELDS, rerank_from_files
    rows = rerank_from_files(Path(args.records), Path(args.membership))
    write_csv(Path(args.out), RANK_FIELDS, rows)
    if args.compare:
        with open(args.compare, encoding="utf-8", newline="") as f:
            saved = list(csv.DictReader(f))
        same = [{k: str(r[k]) for k in RANK_FIELDS} for r in rows] == [{k: r[k] for k in RANK_FIELDS} for r in saved]
        print(f"recomputed {len(rows)} issues; identical to {args.compare}: {same}")
        if not same:
            sys.exit(1)
    else:
        print(f"recomputed {len(rows)} issues -> {args.out}")


def cmd_export_run(args):
    from .orchestrator import export_run
    ctx = make_ctx(args, "export-run")
    print(json.dumps(export_run(ctx)["coverage"], indent=2))


def cmd_export_grading(args):
    from .orchestrator import export_grading
    ctx = make_ctx(args, "export-grading")
    print(json.dumps(export_grading(ctx, Path(args.out), Path(args.before), Path(args.after), Path(args.input)), indent=2))


def cmd_eval_golden(args):
    from .evaluate import evaluate
    from .orchestrator import load_records
    ctx = make_ctx(args, "eval-golden")
    preds = {r["review_id"]: r for r in load_records(ctx)}
    summary = evaluate(Path(args.labels), preds, Path(args.out))
    print(json.dumps({k: v for k, v in summary.items() if "per_class" not in k}, indent=2))


def cmd_injection(args):
    from .evaluate import injection_check
    from .orchestrator import load_records
    ctx = make_ctx(args, "injection-check")
    out = injection_check({r["review_id"]: r for r in load_records(ctx)}, Path(args.expected), Path(args.out))
    print(f"injection/control cases passed: {out['passed']}/{out['cases']} -> {args.out}")


def cmd_planted(args):
    from .verify import planted_error_test
    out = planted_error_test(Path(args.runs_dir) / args.run_id, n_plant=args.n)
    write_json(Path(args.out), out)
    print(f"planted {out['planted']} synthetic label errors; flagged by verifier comparison: {out['flagged']}")


def cmd_trace(args):
    from .common import atomic_write_text
    from .trace import build_trace
    ctx = make_ctx(args, "trace")
    atomic_write_text(Path(args.out), build_trace(ctx))
    print(f"wrote {args.out}")


def cmd_smoke(args):
    """One tiny paid call to confirm the key, model ID and structured output work."""
    from .enrich import SCHEMA, EnrichConfig, Item, validate_output
    from .llm import Request
    cfg = load_config(args.config)
    ecfg = EnrichConfig(cfg)
    client = get_client(args)
    items = [Item("x", "smoke-1", "App crashes every time I open it", False)]
    req = Request("enrich", ecfg.model, ecfg.prompt, ecfg.user_message(items), ecfg.max_tokens(items), SCHEMA,
                  ecfg.temperature, None, 60)
    a = client.call(req)
    print(json.dumps({"ok": a.ok, "error": a.error, "usage": a.usage, "stop_reason": a.stop_reason,
                      "text": a.text[:300], "valid": validate_output(a.text, items, a.stop_reason)[0] if a.ok else None},
                     indent=2, default=str))


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default=str(ROOT / "config" / "pipeline.json"))
    p.add_argument("--state-dir", default=str(ROOT / "state"))
    p.add_argument("--runs-dir", default=str(ROOT / "runs"))
    sub = p.add_subparsers(dest="cmd", required=True)

    def run_args(sp, need_input=True):
        sp.add_argument("--run-id", required=True)
        if need_input:
            sp.add_argument("--input", required=True, help="input CSV with the six source columns")
        sp.add_argument("--budget", type=float, default=None, help="USD cap for this run (default from config)")
        sp.add_argument("--workers", type=int, default=None)
        sp.add_argument("--max-minutes", type=float, default=None)
        sp.add_argument("--fake-model", action="store_true", help="offline deterministic stand-in (tests only)")

    sp = sub.add_parser("make-subset", help="offline: seeded analysis subset that nests the course samples")
    sp.add_argument("--full", default=str(ROOT / "data" / "raw" / "spotify_reviews_18months.csv"))
    sp.add_argument("--out", default=str(ROOT / "data" / "subset_100k.csv"))
    sp.add_argument("--n", type=int, default=100050, help="nonempty reviews to sample (empty texts are added)")
    sp.set_defaults(func=cmd_make_subset)

    sp = sub.add_parser("run", help="MODEL CALLS: run stages on an input CSV (resumable)")
    run_args(sp)
    sp.add_argument("--full-input", default=None,
                    help="full source CSV to profile for ingestion.json when --input is a declared subset")
    sp.add_argument("--stages", default=None, help=f"comma list from {','.join(STAGES)} (default all)")
    sp.add_argument("--mode", choices=("sync", "batch"), default="sync", help="enrichment transport")
    sp.add_argument("--limit-requests", type=int, default=None, help="cap enrichment requests this invocation")
    sp.add_argument("--retry-quarantined", action="store_true", help="re-queue non-empty quarantined records")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("smoke", help="PAID: one tiny call to check credentials and schema")
    sp.add_argument("--fake-model", action="store_true")
    sp.set_defaults(func=cmd_smoke)

    sp = sub.add_parser("status", help="offline: list runs, record statuses and spend")
    sp.set_defaults(func=cmd_status)

    for name, fn in (("rank", cmd_rank), ("export-run", cmd_export_run)):
        sp = sub.add_parser(name, help="offline")
        run_args(sp, need_input=False)
        sp.set_defaults(func=fn)

    sp = sub.add_parser("rerank", help="offline: recompute ranking from saved records + membership")
    sp.add_argument("--records", required=True)
    sp.add_argument("--membership", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--compare", default=None)
    sp.set_defaults(func=cmd_rerank)

    sp = sub.add_parser("export-grading", help="offline: build grading/ for the final run")
    run_args(sp)
    sp.add_argument("--out", default=str(ROOT / "grading"))
    sp.add_argument("--before", required=True)
    sp.add_argument("--after", required=True)
    sp.set_defaults(func=cmd_export_grading)

    sp = sub.add_parser("eval-golden", help="offline: compare human golden labels with saved predictions")
    run_args(sp, need_input=False)
    sp.add_argument("--labels", default=str(ROOT / "evals" / "golden_50_labeled.csv"))
    sp.add_argument("--out", default=str(ROOT / "evals" / "golden"))
    sp.set_defaults(func=cmd_eval_golden)

    sp = sub.add_parser("injection-check", help="offline: score saved outputs on synthetic injection cases")
    run_args(sp, need_input=False)
    sp.add_argument("--expected", default=str(ROOT / "evals" / "injection" / "expected.json"))
    sp.add_argument("--out", default=str(ROOT / "evals" / "injection" / "results.json"))
    sp.set_defaults(func=cmd_injection)

    sp = sub.add_parser("trace", help="offline: trace real reviews through every stage from saved artifacts")
    run_args(sp, need_input=False)
    sp.add_argument("--out", default=str(ROOT / "evals" / "trace.md"))
    sp.set_defaults(func=cmd_trace)

    sp = sub.add_parser("planted-errors", help="offline: planted-error test on saved verifier outputs")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--n", type=int, default=12)
    sp.add_argument("--out", default=str(ROOT / "evals" / "planted_errors.json"))
    sp.set_defaults(func=cmd_planted)

    args = p.parse_args(argv)
    args.func(args)
