# Cost and runtime calculator (100-review pilot)

Specification: `COST_CALCULATOR.md` in the course dataset ZIP.

## Offline replay (default, no API key, no network, standard library only)

```bash
python3 cost/calculator.py
```

This recomputes every charge from the saved usage in `pilot_calls.jsonl` and the editable prices in `rates.csv`, then rewrites `usage.csv`, `report.md`, `report.html` and `replay_result.json`. Opening or importing the calculator never makes a model call.

What-if checks (these write `report_whatif.*` and leave the measured report untouched):

```bash
python3 cost/calculator.py --rate-multiplier 2
```

```bash
python3 cost/calculator.py --rows 1000000 --budget 75
```

With usage held fixed, `--rate-multiplier 2` doubles every API subtotal while measured wall-clock time and the unknown local-compute line stay the same. `--rows` changes only the projection, never the recorded 100-review results.

## Explicit paid pilot (separate command)

```bash
uv run python cost/calculator.py run-pilot --budget 1.00
```

This deletes `cost/pilot_state/` so the cold run starts with an empty result cache. It then runs the real pipeline (ingest, enrich, a declared verification sample, rank, group, memo, export) on `data/raw/cost_100.csv` with one worker, and repeats the same command as the warm run on the saved cache. It saves every attempted call, measures the end-to-end wall clock around each command, and finally runs the offline replay. `--budget` is the USD cap enforced per run by the pipeline's spend reservations.

## Files

| file | contents |
|---|---|
| `pilot_records.jsonl` | one final status per pilot review ID for the cold and warm runs: contract row hash, common labels, result source |
| `pilot_calls.jsonl` | every attempted model call (cold and warm): run, role, model, tier, label_config, review IDs sent, outcome, raw token usage by category, cost, timing, request IDs |
| `pilot_runs.json` | measured wall-clock seconds per run, stage seconds, exact commands, input checksum |
| `rates.csv` | dated per-token prices with source links; standard and Batch API tiers |
| `usage.csv` | one row per call × billing item: billed units × price per unit = item cost |
| `assumptions.json` | projection volumes (from the ingestion report), scenario settings and spending controls |
| `report.md` / `report.html` | the measured-100 dashboard and full-run projections |

Billing items are mutually exclusive. Anthropic reports uncached `input_tokens` separately from `cache_creation_input_tokens` and `cache_read_input_tokens`, so nothing is subtracted or double-counted. Output tokens already include any thinking tokens, and the Haiku stages run without thinking.
