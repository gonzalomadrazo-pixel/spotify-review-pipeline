#!/usr/bin/env python3
"""100-review cost and runtime calculator (COST_CALCULATOR.md).

OFFLINE REPLAY (default; no API key, no network, standard library only):
    python3 cost/calculator.py
    python3 cost/calculator.py --rate-multiplier 2        # doubles API spend; measured time unchanged
    python3 cost/calculator.py --rows 660622               # change projected volume only

EXPLICIT PAID PILOT (separate command; needs ANTHROPIC_API_KEY in .env):
    uv run python cost/calculator.py run-pilot --budget 1.00

Inputs (all editable, all in cost/):
    pilot_calls.jsonl   every attempted call of the real cold and warm pilot runs (usage, timing, ids)
    pilot_records.jsonl one final status per pilot review ID (contract row hash + common labels)
    pilot_runs.json     measured end-to-end wall-clock seconds and stage times per run
    rates.csv           dated per-unit prices with source links
    assumptions.json    projection volumes, scenario multipliers and spending controls
Outputs: usage.csv (one row per call x billing item), report.md, report.html, replay_result.json
Formula: item_cost = billed_units x price_per_unit; total = sum(item_cost). Token categories are
mutually exclusive (uncached input, 5-minute cache write, cache read, output incl. any thinking).
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ITEMS = ("input_uncached", "input_cache_write_5m", "input_cache_read", "output")
USAGE_FIELD = {"input_uncached": "uncached_input_tokens", "input_cache_write_5m": "cache_creation_input_tokens",
               "input_cache_read": "cache_read_input_tokens", "output": "output_tokens"}


# ---------------------------------------------------------------- loading

def load_rates(path, multiplier=1.0):
    rates, meta = {}, {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            key = (r["model"], r["tier"], r["billing_item"])
            rates[key] = float(r["price_usd_per_unit"]) * multiplier
            meta[key] = r
    return rates, meta


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def call_cost(rates, c):
    items = {}
    for item in ITEMS:
        units = int(c.get(USAGE_FIELD[item]) or 0)
        price = rates.get((c["model"], c["tier"], item))
        if price is None:
            raise SystemExit(f"Missing rate for {c['model']}/{c['tier']}/{item} in rates.csv")
        items[item] = (units, price, units * price)
    return items


# ---------------------------------------------------------------- measured pilot

def measured(calls, records, runs, rates):
    usage_rows, stage = [], defaultdict(lambda: defaultdict(float))
    for c in calls:
        items = call_cost(rates, c)
        for item, (units, price, cost) in items.items():
            usage_rows.append({"pilot_run": c["pilot_run"], "request_id": c["request_id"], "role": c["role"],
                               "model": c["model"], "tier": c["tier"], "outcome": c["outcome"],
                               "usage_available": c["usage_available"], "billing_item": item, "billed_units": units,
                               "unit": "token", "price_usd_per_unit": f"{price:.10f}", "item_cost_usd": f"{cost:.10f}"})
        s = stage[(c["pilot_run"], c["role"])]
        s["requests"] += 1
        s["succeeded"] += c["outcome"] == "succeeded"
        s["failed"] += c["outcome"] == "failed"
        s["retries"] += (c.get("attempt") or 1) > 1
        s["review_ids_sent"] += len(c["review_ids"])
        s["unknown_usage"] += not c["usage_available"]
        s["duration_s_sum"] += c.get("duration_s") or 0
        for item, (units, _, cost) in items.items():
            s[item + "_tokens"] += units
            s[item + "_usd"] += cost
        s["api_usd"] += sum(v[2] for v in items.values())
    out = {}
    for run_label, run in runs["runs"].items():
        recs = [r for r in records if r["pilot_run"] == run_label]
        st = defaultdict(int)
        for r in recs:
            st[r["status"]] += 1
        stages = {role: dict(v) for (rl, role), v in stage.items() if rl == run_label}
        api = sum(v["api_usd"] for v in stages.values())
        completed = st["completed"]
        out[run_label] = {
            "wall_clock_s": run["wall_clock_s"], "stage_seconds": run.get("stage_seconds", {}),
            "records": len(recs), "status_counts": dict(st), "unique_texts": len({r["text_sha"] for r in recs}),
            "result_cache_hits": sum(1 for r in recs if str(r.get("result_source", "")).startswith("result_cache")),
            "enrichment_calls": int(stages.get("enrich", {}).get("requests", 0)),
            "stages": stages, "api_usd": api,
            "usd_per_1000_inputs": api / len(recs) * 1000 if recs else None,
            "usd_per_completed_record": api / completed if completed else None,
            "records_per_second": len(recs) / run["wall_clock_s"] if run["wall_clock_s"] else None,
        }
    return out, usage_rows


# ---------------------------------------------------------------- projection

def project(m_cold, a, rates):
    """Extrapolate each stage from its own measured work count; fixed overhead (group, memo) once."""
    e = m_cold["stages"]["enrich"]
    n_texts_pilot = m_cold["unique_texts"]
    req_pilot = e["succeeded"] or 1
    model, tier_std = a["enrich_model"], "standard"
    # Per-text and per-request token profile from the measured cold pilot.
    prefix_per_req = (e["input_cache_read_tokens"] + e["input_cache_write_5m_tokens"]) / req_pilot
    uncached_per_text = e["input_uncached_tokens"] / n_texts_pilot
    out_per_text = e["output_tokens"] / n_texts_pilot
    len_ratio = a["corpus_mean_chars_distinct"] / a["pilot_mean_chars"] if a.get("length_adjust") else 1.0
    out_ratio = (1 + a["quote_share_corpus"]) / (1 + a["quote_share_pilot"]) if a.get("length_adjust") else 1.0

    def enrich_cost(texts, tier, cache_hit_rate, retry_rate):
        reqs = math.ceil(texts / a["batch_size"])
        prefix = reqs * prefix_per_req
        cached, written = prefix * cache_hit_rate, prefix * (1 - cache_hit_rate)
        if a["prefix_cacheable"] is False:
            cached, written, uncached_prefix = 0, 0, prefix
        else:
            uncached_prefix = 0
        uncached = texts * uncached_per_text * len_ratio + uncached_prefix
        output = texts * out_per_text * out_ratio
        k = 1 + retry_rate
        usd = k * (uncached * rates[(model, tier, "input_uncached")] + written * rates[(model, tier, "input_cache_write_5m")]
                   + cached * rates[(model, tier, "input_cache_read")] + output * rates[(model, tier, "output")])
        return usd, reqs, {"uncached_input": uncached * k, "cache_write": written * k, "cache_read": cached * k,
                           "output": output * k}

    def per_text_stage_cost(role, texts):
        s = m_cold["stages"].get(role)
        if not s or not s["review_ids_sent"]:
            return None
        return s["api_usd"] / s["review_ids_sent"] * texts

    def fixed(role):
        s = m_cold["stages"].get(role)
        return s["api_usd"] if s else None

    v = a["volume"]
    distinct = v["distinct_nonempty_texts"] if a["exact_text_reuse"] else v["nonempty_to_classify"]
    measured_hit = e["input_cache_read_tokens"] / max(1, e["input_cache_read_tokens"] + e["input_cache_write_5m_tokens"])
    scenarios = {}
    for name, sc in a["scenarios"].items():
        tier = sc["tier"]
        hit = measured_hit if sc["cache_hit_rate"] == "measured" else float(sc["cache_hit_rate"])
        usd_e, reqs, toks = enrich_cost(distinct, tier, hit, sc["retry_rate"])
        tier_factor = rates[(model, tier, "output")] / rates[(model, tier_std, "output")]
        verify_texts = min(a["verify_max"], max(a["verify_min"], round(sc["verify_fraction"] * distinct)))
        usd_v = (per_text_stage_cost("verify", verify_texts) or 0)
        fb_items = math.ceil(sc["fallback_fraction"] * distinct)
        fb_model = a["fallback_model"]
        fb_per_item = (prefix_per_req + uncached_per_text * len_ratio) * rates[(fb_model, "standard", "input_uncached")] \
            + out_per_text * a["fallback_output_multiplier"] * rates[(fb_model, "standard", "output")]
        usd_fb = fb_items * fb_per_item
        n_issues = a["expected_issue_count"]
        g = m_cold["stages"].get("group")
        usd_g = (g["api_usd"] / g["requests"] * n_issues) if g and g["requests"] else 0.0
        usd_m = (fixed("memo") or 0.0) * (1 + sc["memo_revisions"])
        total = usd_e + usd_v + usd_fb + usd_g + usd_m
        # Time: sync uses measured mean enrichment latency / workers, bounded by output-token rate limit.
        lat = e["duration_s_sum"] / max(1, e["requests"])
        out_per_req = toks["output"] / max(1, reqs)
        rpm_cap = min(a["rate_limits"]["rpm"], a["rate_limits"]["otpm"] / max(1.0, out_per_req))
        workers = a["controls"]["max_workers"]
        if tier == "batch":
            enrich_hours, time_basis = None, "Batch API turnaround not measured: provider states most batches finish within 1 hour, max 24 hours"
        else:
            per_min = min(workers * 60.0 / max(lat, 1e-9), rpm_cap)
            enrich_hours = reqs / per_min / 60.0
            time_basis = f"{reqs} requests / min({workers} workers x 60/{lat:.1f}s, rate-limit cap {rpm_cap:.0f}/min)"
        scenarios[name] = {
            "tier": tier, "distinct_texts_sent": distinct, "enrichment_requests": reqs, "cache_hit_rate": round(hit, 4),
            "retry_rate": sc["retry_rate"], "verify_texts": verify_texts, "fallback_items": fb_items,
            "tokens": {k: round(v) for k, v in toks.items()},
            "usd": {"enrich": usd_e, "verify": usd_v, "fallback": usd_fb, "group_fixed": usd_g, "memo_fixed": usd_m,
                    "total_api": total},
            "usd_per_1000_rows": total / v["rows"] * 1000, "enrich_hours": enrich_hours, "time_basis": time_basis,
            "exceeds_budget": total > a["controls"]["budget_usd"], "tier_output_price_factor": tier_factor,
        }
    return scenarios, {"prefix_tokens_per_request": prefix_per_req, "uncached_input_tokens_per_text": uncached_per_text,
                       "output_tokens_per_text": out_per_text, "length_ratio": len_ratio, "output_ratio": out_ratio,
                       "measured_cache_hit_rate": measured_hit}


# ---------------------------------------------------------------- report

def money(x):
    return "n/a" if x is None else f"${x:,.4f}"


def render(m, scen, profile, a, rate_meta, rate_mult, runs):
    L = []
    L.append("# 100-review pilot: measured cost and runtime, with full-run projections\n")
    L.append(f"Generated by `python3 cost/calculator.py` (offline replay). Rate multiplier: {rate_mult}. "
             f"Input `cost_100.csv` sha256 `{runs['input_sha256']}` (manifest match: {runs.get('manifest_match')}). "
             f"Pilot run at {runs.get('pilot_started_at')} with {runs.get('workers')} worker(s), empty result cache for the cold run.\n")
    L.append("## Measured (real calls)\n")
    L.append("| | cold run | warm run (saved results) |\n|---|---|---|")
    c, w = m.get("cold", {}), m.get("warm", {})
    rows = [("records / unique texts", lambda x: f"{x['records']} / {x['unique_texts']}"),
            ("status counts", lambda x: json.dumps(x["status_counts"])),
            ("result-cache hits (texts)", lambda x: str(x["result_cache_hits"])),
            ("enrichment calls", lambda x: str(x["enrichment_calls"])),
            ("all model calls (attempts)", lambda x: str(int(sum(s["requests"] for s in x["stages"].values())))),
            ("API spend (USD)", lambda x: money(x["api_usd"])),
            ("USD per 1,000 input rows", lambda x: money(x["usd_per_1000_inputs"])),
            ("USD per completed record", lambda x: money(x["usd_per_completed_record"])),
            ("end-to-end wall clock (s)", lambda x: f"{x['wall_clock_s']:.2f}"),
            ("throughput (records/s)", lambda x: f"{x['records_per_second']:.2f}")]
    for label, fn in rows:
        L.append(f"| {label} | {fn(c) if c else '-'} | {fn(w) if w else '-'} |")
    L.append("\n### By stage (cold run)\n")
    L.append("| role | provider / model / tier | effort | prompt+schema | batch size | requests | ok | failed | retries | "
             "reviews sent | uncached in | cache write | cache read | output | USD | summed call s |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for role in ("enrich", "verify", "group", "memo"):
        s = c.get("stages", {}).get(role)
        if not s:
            continue
        info = a["stage_settings"][role]
        L.append(f"| {role} | anthropic / {info['model']} / {info['tier']} | {info['effort']} | {info['version']} | "
                 f"{info['batch_size']} | {int(s['requests'])} | {int(s['succeeded'])} | {int(s['failed'])} | "
                 f"{int(s['retries'])} | {int(s['review_ids_sent'])} | {int(s['input_uncached_tokens']):,} | "
                 f"{int(s['input_cache_write_5m_tokens']):,} | {int(s['input_cache_read_tokens']):,} | "
                 f"{int(s['output_tokens']):,} | {money(s['api_usd'])} | {s['duration_s_sum']:.1f} |")
    L.append("\nStage wall-clock seconds (cold): " + json.dumps(c.get("stage_seconds", {})) +
             "  \nStage wall-clock seconds (warm): " + json.dumps(w.get("stage_seconds", {})))
    L.append("\nSummed call durations are not wall-clock time; wall clock is measured around the whole pipeline command.")
    L.append("\n## Rates used (editable `cost/rates.csv`)\n")
    L.append("| model | tier | item | USD per token | quote | source | checked |\n|---|---|---|---|---|---|---|")
    for (model, tier, item), r in sorted(rate_meta.items()):
        L.append(f"| {model} | {tier} | {item} | {float(r['price_usd_per_unit']) * rate_mult:.10f} | {r['price_quote']} | "
                 f"[link]({r['source_url']}) | {r['checked_on']} |")
    L.append("\n## Full-run projection (estimates, not measurements)\n")
    v = a["volume"]
    L.append(f"Scope: {v['rows']:,} rows accounted for; {v['nonempty_to_classify']:,} nonempty classifications; "
             f"{v['empty_text_quarantines']} empty-text quarantines. Exact-text reuse "
             f"{'ON' if a['exact_text_reuse'] else 'OFF'}: {v['distinct_nonempty_texts']:,} distinct texts sent "
             f"(no-reuse comparison below). Profile from the cold pilot: {json.dumps({k: round(x, 2) for k, x in profile.items()})}.\n")
    L.append("| scenario | tier | texts sent | requests | cache hit | retry | verify texts | fallback items | enrich | verify | "
             "fallback | group (once) | memo (once) | **total API** | per 1k rows | enrich time (h) | budget |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, s in scen.items():
        u = s["usd"]
        L.append(f"| {name} | {s['tier']} | {s['distinct_texts_sent']:,} | {s['enrichment_requests']:,} | {s['cache_hit_rate']} | "
                 f"{s['retry_rate']} | {s['verify_texts']:,} | {s['fallback_items']:,} | {money(u['enrich'])} | "
                 f"{money(u['verify'])} | {money(u['fallback'])} | {money(u['group_fixed'])} | {money(u['memo_fixed'])} | "
                 f"**{money(u['total_api'])}** | {money(s['usd_per_1000_rows'])} | "
                 f"{'n/a' if s['enrich_hours'] is None else round(s['enrich_hours'], 2)} | "
                 f"{'EXCEEDS BUDGET' if s['exceeds_budget'] else 'within budget'} |")
    L.append("\nTime basis per scenario: " + "; ".join(f"{k}: {s['time_basis']}" for k, s in scen.items()))
    ctl = a["controls"]
    L.append(f"\n## Controls\n\nBudget cap ${ctl['budget_usd']:.2f} (pipeline `--budget`, enforced by reservations before "
             f"dispatch) · output-token cap {ctl['output_token_cap']} per call · max workers {ctl['max_workers']} · "
             f"max fallback fraction {ctl['max_fallback_fraction']} · invalid-output retries 1 · transient retries 4 with "
             f"exponential backoff and jitter.")
    L.append("\n## API spend vs. other costs\n\n" + a["local_compute_note"])
    L.append("\n## Formulas\n\n`item_cost = billed_units x price_per_unit` per call and billing item (see `usage.csv`); "
             "projections scale each stage by its own work count: enrichment by distinct texts and requests "
             "(ceil(texts/batch_size)), verification by sampled texts, fallback by capped items, group by issue count "
             "and memo once.")
    return "\n".join(L) + "\n"


def to_html(md):
    body = []
    for line in md.splitlines():
        if line.startswith("|") and set(line.replace("|", "").strip()) <= set("-: "):
            continue
        if line.startswith("|"):
            cells = [html.escape(x.strip()) for x in line.strip("|").split("|")]
            body.append("<tr>" + "".join(f"<td>{x}</td>" for x in cells) + "</tr>")
        elif line.startswith("#"):
            lvl = len(line) - len(line.lstrip("#"))
            body.append(f"<h{lvl}>{html.escape(line.lstrip('#').strip())}</h{lvl}>")
        elif line.strip():
            body.append(f"<p>{html.escape(line)}</p>")
    out, in_table = [], False
    for b in body:
        if b.startswith("<tr>") and not in_table:
            out.append("<table>")
            in_table = True
        if not b.startswith("<tr>") and in_table:
            out.append("</table>")
            in_table = False
        out.append(b)
    if in_table:
        out.append("</table>")
    css = ("body{font-family:system-ui,sans-serif;max-width:1200px;margin:24px auto;padding:0 16px;color:#111;background:#fff}"
           "table{border-collapse:collapse;font-size:12px;margin:8px 0}td{border:1px solid #ccc;padding:3px 6px}"
           "tr:first-child td{font-weight:600;background:#f3f3f3}")
    return f"<!doctype html><meta charset='utf-8'><title>Pilot cost report</title><style>{css}</style>" + "\n".join(out)


def replay(args):
    a = json.loads((HERE / "assumptions.json").read_text())
    if args.rows:
        a["volume"]["rows"] = args.rows
    if args.budget is not None:
        a["controls"]["budget_usd"] = args.budget
    rates, meta = load_rates(HERE / "rates.csv", args.rate_multiplier)
    calls = load_jsonl(HERE / "pilot_calls.jsonl")
    records = load_jsonl(HERE / "pilot_records.jsonl")
    runs = json.loads((HERE / "pilot_runs.json").read_text())
    m, usage_rows = measured(calls, records, runs, rates)
    scen, profile = project(m["cold"], a, rates)
    with open(HERE / "usage.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(usage_rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(usage_rows)
    md = render(m, scen, profile, a, meta, args.rate_multiplier, runs)
    suffix = "" if args.rate_multiplier == 1 and not args.rows and args.budget is None else "_whatif"
    (HERE / f"report{suffix}.md").write_text(md, encoding="utf-8")
    (HERE / f"report{suffix}.html").write_text(to_html(md), encoding="utf-8")
    result = {"rate_multiplier": args.rate_multiplier, "measured": m, "projection": scen, "profile": profile}
    (HERE / f"replay_result{suffix}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"cold API ${m['cold']['api_usd']:.6f}  wall {m['cold']['wall_clock_s']:.2f}s | warm API ${m['warm']['api_usd']:.6f}  "
          f"wall {m['warm']['wall_clock_s']:.2f}s  enrichment calls warm={m['warm']['enrichment_calls']}")
    for k, s in scen.items():
        print(f"  {k:<28} total ${s['usd']['total_api']:,.2f}  {'EXCEEDS BUDGET' if s['exceeds_budget'] else 'ok'}")
    print(f"wrote cost/report{suffix}.md, cost/report{suffix}.html, cost/usage.csv")


# ---------------------------------------------------------------- explicit paid pilot

def run_pilot(args):
    sys.path.insert(0, str(ROOT))
    from pipeline.common import sha256_file  # noqa: E402
    inp = ROOT / "data" / "raw" / "cost_100.csv"
    manifest = json.loads((ROOT / "data" / "raw" / "manifest.json").read_text())
    sha = sha256_file(inp)
    if sha != manifest["files"]["cost_100.csv"]["sha256"]:
        raise SystemExit("cost_100.csv does not match manifest checksum")
    state, runs_dir = HERE / "pilot_state", HERE / "pilot_runs"
    if state.exists() and not args.keep_state:
        shutil.rmtree(state)  # the cold run must start with an empty result cache
    if runs_dir.exists() and not args.keep_state:
        shutil.rmtree(runs_dir)
    meta = {"input": "data/raw/cost_100.csv", "input_sha256": sha, "manifest_match": True, "workers": 1,
            "pilot_started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "runs": {}}
    for label in ("cold", "warm"):
        cmd = [sys.executable, "-m", "pipeline", "--state-dir", str(state), "--runs-dir", str(runs_dir), "run",
               "--run-id", f"pilot-{label}", "--input", str(inp), "--workers", "1", "--budget", str(args.budget)]
        t0 = time.perf_counter()
        subprocess.run(cmd, cwd=ROOT, check=True)
        wall = time.perf_counter() - t0
        summary = json.loads((runs_dir / f"pilot-{label}" / "run_summary.json").read_text())
        stage_s = {}
        for s in summary["stages"]:
            stage_s[s["stage"]] = round(stage_s.get(s["stage"], 0) + (s.get("seconds") or 0), 3)
        meta["runs"][label] = {"run_id": f"pilot-{label}", "wall_clock_s": round(wall, 3), "stage_seconds": stage_s,
                               "command": " ".join(cmd[1:])}
    (HERE / "pilot_runs.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    collect(runs_dir)
    shutil.copyfile(ROOT / "config" / "rates.csv", HERE / "rates.csv") if not (HERE / "rates.csv").exists() else None
    replay(argparse.Namespace(rate_multiplier=1.0, rows=None, budget=None))


def collect(runs_dir):
    """Copy pilot evidence into cost/: per-ID records and every attempted call (cold + warm)."""
    recs, calls = [], []
    for label in ("cold", "warm"):
        d = runs_dir / f"pilot-{label}"
        rich = {r["review_id"]: r for r in load_jsonl(d / "enriched.jsonl")}
        for r in load_jsonl(d / "records.jsonl"):
            x = rich.get(r["review_id"], {})
            recs.append({"pilot_run": label, **r, "subtopic": x.get("subtopic"), "result_source": x.get("result_source"),
                         "text_sha": None})
        for c in load_jsonl(d / "calls.jsonl"):
            calls.append({"pilot_run": label, **c})
    # text hashes for unique-text counts (from the state DB rows)
    import sqlite3
    db = sqlite3.connect(HERE / "pilot_state" / "state.sqlite")
    sha = dict(db.execute("SELECT review_id, text_sha FROM rows"))
    for r in recs:
        r["text_sha"] = sha.get(r["review_id"])
    with open(HERE / "pilot_records.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)
    with open(HERE / "pilot_calls.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(c, ensure_ascii=False) + "\n" for c in calls)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", default="replay", choices=("replay", "run-pilot"))
    p.add_argument("--rate-multiplier", type=float, default=1.0)
    p.add_argument("--rows", type=int, default=None)
    p.add_argument("--budget", type=float, default=None, help="replay: budget for warnings; run-pilot: USD cap per run")
    p.add_argument("--keep-state", action="store_true")
    args = p.parse_args()
    if args.command == "run-pilot":
        if args.budget is None:
            raise SystemExit("run-pilot is a paid command: pass an explicit --budget (USD cap per pilot run)")
        run_pilot(args)
    else:
        replay(args)


if __name__ == "__main__":
    main()
