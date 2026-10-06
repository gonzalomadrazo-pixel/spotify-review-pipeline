# Setup and Run Guide

This document walks you through the steps to run the Spotify review pipeline from smoke test through full analysis.

## Prerequisites

- Data files unpacked into `data/raw/` (see README.md for SHA-256 checksums)
- `uv sync` already run
- Git commit `bcdb41a` or later

## Step 1: Set up your API key (5 minutes)

```bash
# Copy the template
cp .env.example .env

# Edit .env and add your Anthropic API key
# (opens in your default editor)
```

The `.env` file is git-ignored and will never be committed.

Verify the key works:
```bash
uv run python -m pipeline smoke
```

Expected output: `"ok": true`, usage and token counts, and a valid structured output.

## Step 2: Decide your budget

For reference (pre-pilot estimates; the pilot replaces these with real numbers):
- **100-review pilot:** < $1
- **500-review run:** a few dollars
- **10,000-review run:** ~$2–5
- **Golden set evaluation:** none (code only)
- **Full 660k run (Batch API):** ~$55–70
- **Full 660k run (standard):** ~$100–130

**Recommendation:** start with the 500-review run (about $3) to see label quality, then decide on the full run.

## Step 3: Label the golden 50 reviews (30–45 minutes)

Open the labeling page in your browser:
```bash
open evals/golden_labeler.html
```

- Label all 50 reviews (topic, intent, severity, sentiment, evidence quote)
- Progress auto-saves in your browser
- Click "Export CSV" when done
- Save the file as `evals/golden_50_labeled.csv`

## Step 4: Run the 100-review cold/warm pilot (15 minutes)

The pilot measures real API usage and runtime, then generates the cost report.

```bash
uv run python cost/calculator.py run-pilot --budget 1.00
```

This will:
1. Run ingest → enrich → verify → rank → group → memo → export on 100 reviews (cold)
2. Repeat the same run on the saved cache (warm)
3. Generate `cost/report.md` with measured vs. projected costs

Open the report:
```bash
open cost/report.md
```

## Step 5: Run the 500-review checkpoint (5–15 minutes)

This is your first large run and covers Class 7 deliverables.

```bash
uv run python -m pipeline run \
  --run-id checkpoint500 \
  --input data/raw/checkpoint_500.csv \
  --budget 5.00 \
  --workers 2
```

The run will:
- Ingest 500 reviews
- Enrich all distinct texts
- Verify a sample
- Rank, group, and memo the findings
- Export all results to `runs/checkpoint500/`

Check the results:
```bash
cat runs/checkpoint500/run_summary.json
ls runs/checkpoint500/
```

**Evaluate against golden labels** (once you've labeled them):
```bash
uv run python -m pipeline eval-golden --run-id checkpoint500
cat evals/golden/golden_summary.json
```

## Step 6 (Optional): Run the 10,000-review analysis run (30 minutes – 1 hour)

```bash
uv run python -m pipeline run \
  --run-id analysis10k \
  --input data/raw/analysis_10000.csv \
  --budget 15.00 \
  --workers 4
```

## Step 7: Full 660k run with interrupt/resume recording

This is the production run for your final deliverable.

**Option A: Use the Batch API (recommended; half price, async)**

```bash
uv run python -m pipeline run \
  --run-id full \
  --input data/raw/spotify_reviews_18months.csv \
  --budget 80 \
  --workers 8 \
  --mode batch
```

The Batch API is asynchronous and may take up to 24 hours, but it's half the price.

**Option B: Use standard rate-limited API**

```bash
uv run python -m pipeline run \
  --run-id full \
  --input data/raw/spotify_reviews_18months.csv \
  --budget 150 \
  --workers 8
```

### Recording the interrupt/resume (5 minutes)

While the full run is in progress (or after ingest completes), test graceful interruption:

1. **Record your terminal** (e.g., `asciinema rec`, or a screenshot/video of the terminal)
2. **Press Ctrl-C** to signal stop (in-flight calls finish and are saved)
3. **Wait 10 seconds** for the graceful shutdown
4. **Resume the same command:**

```bash
uv run python -m pipeline run \
  --run-id full \
  --input data/raw/spotify_reviews_18months.csv \
  --budget 150 \
  --workers 8
```

The run will resume from exactly where it left off: no completed reviews are re-sent.

Save the recording as `evidence/interrupt_resume_recording.mp4` (or `.gif`, `.txt` if it's a terminal transcript).

## Step 8: Grading export

```bash
uv run python -m pipeline export-grading \
  --run-id full \
  --input data/raw/spotify_reviews_18months.csv \
  --out grading \
  --before runs/full/checkpoints/inv01_<reason>.json \
  --after runs/full/checkpoints/inv02_completed.json
```

The `grading/` folder is ready for submission.

Verify it passes the course checker:
```bash
cd grading && python3 ../data/raw/check_submission.py check \
  --reference ../data/raw/reference.json \
  --submission . \
  --out self-check.json
cat self-check.json
```

## Step 9: Final verification and injection tests

```bash
# Planted-error test (verifier catches corrupted labels)
uv run python -m pipeline planted-errors --run-id full --n 12

# Injection/control cases (real model vs. adversarial input)
uv run python -m pipeline injection-check --run-id full
```

## Troubleshooting

**"ANTHROPIC_API_KEY not found"**
→ Check `.env` exists and contains `ANTHROPIC_API_KEY=sk-...`

**"Refusal" or "Unknown model" errors**
→ Verify your key is live (check platform.anthropic.com account page)

**"Budget cap" stops the run early**
→ Increase `--budget` or re-run to resume on the exact same cached work

**Large runs take longer than expected**
→ The Batch API can take 1–24 hours. Standard API is faster but more expensive. Check `cost/report.md` for timing projections.

## Commands at a glance

```bash
# Smoke test
uv run python -m pipeline smoke

# Pilot (measured costs)
uv run python cost/calculator.py run-pilot --budget 1.00

# 500-review run
uv run python -m pipeline run --run-id checkpoint500 --input data/raw/checkpoint_500.csv --budget 5.00 --workers 2

# Evaluate golden
uv run python -m pipeline eval-golden --run-id checkpoint500

# Full run (Batch API, recommended)
uv run python -m pipeline run --run-id full --input data/raw/spotify_reviews_18months.csv --budget 80 --workers 8 --mode batch

# Export for grading
uv run python -m pipeline export-grading --run-id full --input data/raw/spotify_reviews_18months.csv --out grading --before runs/full/checkpoints/inv01_request_limit.json --after runs/full/checkpoints/inv02_completed.json

# Verify grading folder
python3 data/raw/check_submission.py check --reference data/raw/reference.json --submission grading --out self-check.json
```

## What gets saved

After each run, `runs/<run-id>/` contains:

- `records.jsonl` – all 660k+ rows with labels (contract format)
- `enriched.jsonl` – labeled records with internal details
- `membership.csv` – issue membership
- `ranking.csv` – issue priority ranking
- `issues.json` – grouping agent's titles and summaries
- `facts.json` – all numerical aggregates with fact IDs
- `claims.csv` – facts cited in the memo
- `memo.md` – the final recommendation
- `verify/` – verifier predictions and disagreements
- `run_summary.json` – accounting and timing
- `run_log.jsonl` – event log

`grading/` (export-grading) contains the submission folder for the course checker.

`evals/` contains golden-set evaluation, planted-error results, and injection-check results.

## Next steps

1. **Now:** Set up `.env` with your API key
2. **Then:** Decide on budget (start with 500-review run at $3–5)
3. **Then:** Label the 50 golden reviews
4. **Then:** Smoke test → pilot → 500-review run
5. **Finally:** Full 660k run + grading export + verification

Questions? See README.md for architecture, design choices, and the evidence map.
