#!/usr/bin/env python3
"""100-review cost and runtime calculator (COST_CALCULATOR.md).

OFFLINE REPLAY (default; no API key, no network, standard library only):
    python3 cost/calculator.py
    python3 cost/calculator.py --rate-multiplier 2        # doubles API spend; measured time unchanged
    python3 cost/calculator.py --rows 660622               # change projected volume only

EXPLICIT PILOT EXECUTION (separate command; makes real model calls with the configured providers.
The default config uses a local Ollama model, so API spend is $0; roles set to Anthropic need ANTHROPIC_API_KEY):
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

def stage_cost(s, rates, model, tier, scale=1.0, prefix_cacheable=True):
    """Re-price a measured stage's token counts at another model/tier (used for modeled alternatives)."""
    if not s:
        return 0.0
    cached = s["input_cache_read_tokens"] + s["input_cache_write_5m_tokens"]
    if prefix_cacheable:
        usd = (s["input_uncached_tokens"] * rates[(model, tier, "input_uncached")]
               + s["input_cache_write_5m_tokens"] * rates[(model, tier, "input_cache_write_5m")]
               + s["input_cache_read_tokens"] * rates[(model, tier, "input_cache_read")])
    else:
        usd = (s["input_uncached_tokens"] + cached) * rates[(model, tier, "input_uncached")]
    return scale * (usd + s["output_tokens"] * rates[(model, tier, "output")])


def project(m_cold, a, rates, v):
    """Extrapolate each stage from its own measured work count; fixed overhead (group, memo) once."""
    st = m_cold["stages"]
    e = st["enrich"]
    n_texts_pilot = m_cold["unique_texts"]
    req_pilot = e["succeeded"] or 1
    prefix_per_req = (e["input_cache_read_tokens"] + e["input_cache_write_5m_tokens"]) / req_pilot
    uncached_per_text = e["input_uncached_tokens"] / n_texts_pilot
    out_per_text = e["output_tokens"] / n_texts_pilot
    len_ratio = v["mean_chars_distinct"] / a["pilot_mean_chars"] if a.get("length_adjust") else 1.0
    measured_hit = e["input_cache_read_tokens"] / (req_pilot * prefix_per_req) if prefix_per_req else 0.0
    distinct = v["distinct_nonempty_texts"] if a["exact_text_reuse"] else v["nonempty_to_classify"]
    lc = a["local_compute"]

    def lat(role):
        s = st.get(role)
        return (s["duration_s_sum"] / s["requests"]) if s and s["requests"] else 0.0

    scenarios = {}
    for name, sc in a["scenarios"].items():
        model, tier, provider = sc["model"], sc["tier"], sc.get("provider", "anthropic")
        cacheable = sc.get("prefix_cacheable", a["prefix_cacheable"])
        hit = measured_hit if sc["cache_hit_rate"] == "measured" else float(sc["cache_hit_rate"])
        reqs = math.ceil(distinct / a["batch_size"])
        k = 1 + sc["retry_rate"]
        prefix = reqs * prefix_per_req
        cached, written = (prefix * hit, prefix * (1 - hit)) if cacheable else (0.0, 0.0)
        uncached = distinct * uncached_per_text * len_ratio + (0.0 if cacheable else prefix)
        output = distinct * out_per_text
        price = lambda item: rates[(model, tier, item)]
        usd_e = k * (uncached * price("input_uncached") + written * price("input_cache_write_5m")
                     + cached * price("input_cache_read") + output * price("output"))
        verify_texts = min(a["verify_max"], max(a["verify_min"], round(sc["verify_fraction"] * distinct)))
        v_sent = (st.get("verify") or {}).get("review_ids_sent") or 0
        usd_v = stage_cost(st.get("verify"), rates, model, tier, verify_texts / v_sent, cacheable) if v_sent else 0.0
        fb_items = math.ceil(sc["fallback_fraction"] * distinct)
        fb_model = a["fallback_model"] if provider == "ollama" else model
        fb_per_item = (prefix_per_req + uncached_per_text * len_ratio) * rates[(fb_model, "standard", "input_uncached")] \
            + out_per_text * a["fallback_output_multiplier"] * rates[(fb_model, "standard", "output")]
        usd_fb = fb_items * fb_per_item
        g = st.get("group")
        n_issues = a["expected_issue_count"]
        usd_g = stage_cost(g, rates, model, tier, n_issues / g["requests"], cacheable) if g and g["requests"] else 0.0
        usd_m = stage_cost(st.get("memo"), rates, model, tier, 1.0, cacheable) * (1 + sc["memo_revisions"]) / max(
            1, (st.get("memo") or {}).get("requests", 1))
        total = usd_e + usd_v + usd_fb + usd_g + usd_m
        workers = a["controls"]["max_workers"]
        if provider == "ollama":
            secs = {"enrich": k * reqs * lat("enrich") / workers,
                    "verify": math.ceil(verify_texts / a["batch_size"]) * lat("verify"),
                    "fallback": fb_items * lat("enrich") / a["batch_size"] * 2,
                    "group": n_issues * lat("group"), "memo": (1 + sc["memo_revisions"]) * lat("memo")}
            hours = sum(secs.values()) / 3600
            kwh = hours * lc["watts_under_load"] / 1000
            local = {"hours": hours, "kwh": kwh, "usd_estimate": kwh * lc["usd_per_kwh"]}
            time_basis = (f"{reqs:,} enrich requests x {lat('enrich'):.1f}s measured mean latency x (1+retry) / {workers} "
                          f"worker(s) + verify/fallback/group/memo at their measured latencies")
        else:
            hours, local = None, {"hours": None, "kwh": None, "usd_estimate": None}
            time_basis = "not measured for this provider (modeled alternative)"
        scenarios[name] = {
            "provider": provider, "model": model, "tier": tier, "distinct_texts_sent": distinct,
            "enrichment_requests": reqs, "cache_hit_rate": round(hit, 4), "retry_rate": sc["retry_rate"],
            "verify_texts": verify_texts, "fallback_items": fb_items,
            "tokens": {"uncached_input": round(uncached * k), "cache_write": round(written * k),
                       "cache_read": round(cached * k), "output": round(output * k)},
            "usd": {"enrich": usd_e, "verify": usd_v, "fallback": usd_fb, "group_fixed": usd_g, "memo_fixed": usd_m,
                    "total_api": total},
            "usd_per_1000_rows": total / v["rows"] * 1000, "local_hours": hours, "local_compute": local,
            "time_basis": time_basis, "exceeds_budget": total > a["controls"]["budget_usd"],
            "note": sc.get("note", "")}
    return scenarios, {"prefix_tokens_per_request": prefix_per_req, "uncached_input_tokens_per_text": uncached_per_text,
                       "output_tokens_per_text": out_per_text, "input_length_ratio": len_ratio,
                       "measured_prefix_cache_hit_rate": measured_hit}


# ---------------------------------------------------------------- report

def money(x):
    return "n/a" if x is None else f"${x:,.4f}"


def render(m, projections, a, rate_meta, rate_mult, runs):
    L = []
    L.append("# 100-review pilot: measured cost and runtime, with projections\n")
    L.append(f"Generated by `python3 cost/calculator.py` (offline replay). Rate multiplier: {rate_mult}. "
             f"Input `cost_100.csv` sha256 `{runs['input_sha256']}` (manifest match: {runs.get('manifest_match')}). "
             f"Pilot run at {runs.get('pilot_started_at')} with {runs.get('workers')} worker(s), empty result cache for the cold run.\n")
    L.append("## Measured (real calls)\n")
    L.append("| | cold run | warm run (saved results) |\n|---|---|---|")
    c, w = m.get("cold", {}), m.get("warm", {})
    rows = [("records / unique texts", lambda x: f"{x['records']} / {x['unique_texts']}"),
            ("status counts", lambda x: json.dumps(x["status_counts"])),
            ("result-cache hits (records)", lambda x: str(x["result_cache_hits"])),
            ("enrichment calls", lambda x: str(x["enrichment_calls"])),
            ("all model calls (attempts)", lambda x: str(int(sum(s["requests"] for s in x["stages"].values())))),
            ("API spend (USD)", lambda x: money(x["api_usd"])),
            ("USD per 1,000 input rows", lambda x: money(x["usd_per_1000_inputs"])),
            ("USD per completed record", lambda x: money(x["usd_per_completed_record"])),
            ("end-to-end wall clock (s)", lambda x: f"{x['wall_clock_s']:.2f}"),
            ("throughput (records/s)", lambda x: f"{x['records_per_second']:.3f}")]
    for label, fn in rows:
        L.append(f"| {label} | {fn(c) if c else '-'} | {fn(w) if w else '-'} |")
    cfg = runs.get("config", {})
    L.append("\n### By stage (cold run)\n")
    L.append("| role | provider / model / tier | effort / thinking | prompt+schema | batch size | requests | ok | failed | "
             "retries | reviews sent | uncached in | cache write | cache read | output | USD | summed call s |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for role in ("enrich", "verify", "group", "memo"):
        s = c.get("stages", {}).get(role)
        if not s:
            continue
        rc = cfg.get(role, {})
        tiers = sorted({x["tier"] for x in runs.get("_calls", []) if x["role"] == role}) or ["standard"]
        effort = rc.get("effort") or ("think=false (Ollama)" if rc.get("provider") == "ollama" else "none")
        version = "+".join(x for x in (rc.get("prompt_version"), rc.get("schema_version")) if x)
        batch = rc.get("batch_size", "1 issue" if role == "group" else "1 memo")
        L.append(f"| {role} | {rc.get('provider', '?')} / {rc.get('model', '?')} / {','.join(tiers)} | {effort} | {version} | "
                 f"{batch} | {int(s['requests'])} | {int(s['succeeded'])} | {int(s['failed'])} | "
                 f"{int(s['retries'])} | {int(s['review_ids_sent'])} | {int(s['input_uncached_tokens']):,} | "
                 f"{int(s['input_cache_write_5m_tokens']):,} | {int(s['input_cache_read_tokens']):,} | "
                 f"{int(s['output_tokens']):,} | {money(s['api_usd'])} | {s['duration_s_sum']:.1f} |")
    L.append("\nStage wall-clock seconds (cold): " + json.dumps(c.get("stage_seconds", {})) +
             "  \nStage wall-clock seconds (warm): " + json.dumps(w.get("stage_seconds", {})))
    L.append("\nSummed call durations are not wall-clock time; wall clock is measured around the whole pipeline command. "
             "Cache read = prompt prefix reused from the provider/KV cache (separate from the saved-result cache).")
    L.append("\n## Rates used (editable `cost/rates.csv`)\n")
    L.append("| model | tier | item | USD per token | quote | source | checked |\n|---|---|---|---|---|---|---|")
    for (model, tier, item), r in sorted(rate_meta.items()):
        L.append(f"| {model} | {tier} | {item} | {float(r['price_usd_per_unit']) * rate_mult:.10f} | {r['price_quote']} | "
                 f"[link]({r['source_url']}) | {r['checked_on']} |")
    for vname, (v, scen, profile) in projections.items():
        L.append(f"\n## Projection: {vname} (estimates, not measurements)\n")
        L.append(f"{v['label']}. {v['rows']:,} rows accounted for; {v['nonempty_to_classify']:,} nonempty classifications; "
                 f"{v['empty_text_quarantines']} empty-text quarantines. Exact-text reuse "
                 f"{'ON' if a['exact_text_reuse'] else 'OFF'}: {v['distinct_nonempty_texts']:,} distinct texts sent "
                 f"(no reuse would send {v['nonempty_to_classify']:,}). Profile from the cold pilot: "
                 f"{json.dumps({k: round(x, 3) for k, x in profile.items()})}.\n")
        L.append("| scenario | provider / model / tier | texts sent | requests | prefix cache hit | retry | verify texts | "
                 "fallback items | enrich | verify | fallback | group (once) | memo (once) | **total API** | per 1k rows | "
                 "local hours | local electricity (est.) | budget |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for name, s in scen.items():
            u, lcx = s["usd"], s["local_compute"]
            L.append(f"| {name} | {s['provider']} / {s['model']} / {s['tier']} | {s['distinct_texts_sent']:,} | "
                     f"{s['enrichment_requests']:,} | {s['cache_hit_rate']} | {s['retry_rate']} | {s['verify_texts']:,} | "
                     f"{s['fallback_items']:,} | {money(u['enrich'])} | {money(u['verify'])} | {money(u['fallback'])} | "
                     f"{money(u['group_fixed'])} | {money(u['memo_fixed'])} | **{money(u['total_api'])}** | "
                     f"{money(s['usd_per_1000_rows'])} | {'n/a' if s['local_hours'] is None else round(s['local_hours'], 1)} | "
                     f"{'n/a' if lcx['usd_estimate'] is None else money(lcx['usd_estimate'])} | "
                     f"{'EXCEEDS BUDGET' if s['exceeds_budget'] else 'within budget'} |")
        notes = [f"{k}: {s['time_basis']}" + (f" ({s['note']})" if s["note"] else "") for k, s in scen.items()]
        L.append("\nBasis: " + "; ".join(notes))
    ctl = a["controls"]
    L.append(f"\n## Controls\n\nBudget cap ${ctl['budget_usd']:.2f} (pipeline `--budget`, enforced by reservations before "
             f"dispatch) · output-token cap {ctl['output_token_cap']} per call · max workers {ctl['max_workers']} · "
             f"max fallback fraction {ctl['max_fallback_fraction']} · invalid-output retries 1 · transient retries 4 with "
             f"exponential backoff and jitter.\n\nConcurrency decision: {a.get('worker_decision', '')}")
    L.append("\n## API spend vs. other costs\n\n" + a["local_compute_note"] + " Local compute assumptions: "
             + json.dumps(a["local_compute"]))
    L.append("\n## Formulas\n\n`item_cost = billed_units x price_per_unit` per call and billing item (see `usage.csv`); "
             "projections scale each stage by its own work count: enrichment by distinct texts and requests "
             "(ceil(texts/batch_size)), verification by sampled texts, fallback by capped items, group by issue count "
             "and memo once. Local electricity = local hours x watts / 1000 x USD per kWh (assumptions).")
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
    if args.rows:  # scale the primary scope's counts to a different projected volume
        v = a["volumes"][a["primary_volume"]]
        f = args.rows / v["rows"]
        v.update({"rows": args.rows, "nonempty_to_classify": round(v["nonempty_to_classify"] * f),
                  "distinct_nonempty_texts": round(v["distinct_nonempty_texts"] * f),
                  "label": v["label"] + f" (scaled to {args.rows:,} rows)"})
    if args.budget is not None:
        a["controls"]["budget_usd"] = args.budget
    rates, meta = load_rates(HERE / "rates.csv", args.rate_multiplier)
    calls = load_jsonl(HERE / "pilot_calls.jsonl")
    records = load_jsonl(HERE / "pilot_records.jsonl")
    runs = json.loads((HERE / "pilot_runs.json").read_text())
    m, usage_rows = measured(calls, records, runs, rates)
    runs["_calls"] = calls
    projections = {name: (v, *project(m["cold"], a, rates, v)) for name, v in a["volumes"].items()}
    scen = projections[a["primary_volume"]][1]
    with open(HERE / "usage.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(usage_rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(usage_rows)
    md = render(m, projections, a, meta, args.rate_multiplier, runs)
    runs.pop("_calls", None)
    suffix = "" if args.rate_multiplier == 1 and not args.rows and args.budget is None else "_whatif"
    (HERE / f"report{suffix}.md").write_text(md, encoding="utf-8")
    (HERE / f"report{suffix}.html").write_text(to_html(md), encoding="utf-8")
    result = {"rate_multiplier": args.rate_multiplier, "measured": m,
              "projections": {k: {"volume": v, "scenarios": sc, "profile": pr} for k, (v, sc, pr) in projections.items()}}
    (HERE / f"replay_result{suffix}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"cold API ${m['cold']['api_usd']:.6f}  wall {m['cold']['wall_clock_s']:.2f}s | warm API ${m['warm']['api_usd']:.6f}  "
          f"wall {m['warm']['wall_clock_s']:.2f}s  enrichment calls warm={m['warm']['enrichment_calls']}")
    for k, s in scen.items():
        hrs = "" if s["local_hours"] is None else f"  local {s['local_hours']:.1f} h"
        print(f"  {k:<30} API ${s['usd']['total_api']:,.2f}{hrs}  {'EXCEEDS BUDGET' if s['exceeds_budget'] else 'ok'}")
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
    cfg = json.loads((ROOT / "config" / "pipeline.json").read_text())
    meta = {"input": "data/raw/cost_100.csv", "input_sha256": sha, "manifest_match": True, "workers": 1, "config": cfg,
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
    shutil.copyfile(ROOT / "config" / "rates.csv", HERE / "rates.csv")
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
            raise SystemExit("run-pilot makes model calls: pass an explicit --budget (USD cap per pilot run)")
        run_pilot(args)
    else:
        replay(args)


if __name__ == "__main__":
    main()
