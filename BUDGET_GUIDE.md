# Budget Decision Guide

Choose your budget based on what you want to accomplish and what you're willing to spend. All prices use **Haiku 4.5** at standard rates; the Batch API cuts these in half.

## Pre-pilot estimates (will be replaced by real measurements)

| Run | Rows | Duration | Cost (std) | Cost (Batch) | Purpose |
|---|---|---|---|---|---|
| Smoke test | 1 | 1 min | $0.01 | $0.01 | Verify API key works |
| 100-review pilot | 100 | 15 min | $0.06 | $0.03 | Measure real costs |
| 500-review | 500 | 5–15 min | $3–5 | $1.50–2.50 | **Class 7 deliverable** |
| 10,000-review | 10,000 | 30 min–1 h | $15–20 | $8–10 | Optional validation run |
| **Golden set** | 50 | 2 min | $0 | $0 | Evaluation (code only) |
| **Full 660k** | 660,622 | 2–6 h (Batch) or 1–2 h (std) | $100–130 | $55–70 | **Production run** |

## Recommended pathway

### Minimum (just Class 7 deliverable): **$5 budget**

```bash
# Step 1: Smoke test + pilot
uv run python -m pipeline smoke                              # $0.01
uv run python cost/calculator.py run-pilot --budget 1.00     # <$1

# Step 2: 500-review run
uv run python -m pipeline run \
  --run-id checkpoint500 \
  --input data/raw/checkpoint_500.csv \
  --budget 5.00 \
  --workers 2
# Cost: $3–5

# Step 3: Grading export
uv run python -m pipeline export-grading \
  --run-id checkpoint500 \
  --input data/raw/checkpoint_500.csv \
  --out grading \
  --before runs/checkpoint500/checkpoints/inv01_*.json \
  --after runs/checkpoint500/checkpoints/inv02_*.json
```

**Result:** `grading/` folder ready for submission. Confidence in label quality = measured on 500 rows only.

---

### Comprehensive (full analysis + production evidence): **$90–150 budget**

```bash
# Steps 1–3: Same as above ($5)

# Step 4: Optional validation on 10,000 rows
uv run python -m pipeline run \
  --run-id analysis10k \
  --input data/raw/analysis_10000.csv \
  --budget 20.00 \
  --workers 4
# Cost: $8–10 (Batch) or $15–20 (standard)

# Step 5: Full production run (Batch API recommended)
uv run python -m pipeline run \
  --run-id full \
  --input data/raw/spotify_reviews_18months.csv \
  --budget 80 \  # ← Batch API
  --workers 8 \
  --mode batch
# Cost: $55–70 (Batch) or $100–130 (standard)

# Step 6: Grading export from full run
uv run python -m pipeline export-grading \
  --run-id full \
  --input data/raw/spotify_reviews_18months.csv \
  --out grading \
  --before runs/full/checkpoints/inv01_*.json \
  --after runs/full/checkpoints/inv02_*.json
```

**Result:** `grading/` folder with all 660k+ rows analyzed. Full confidence in label quality and product recommendation. Interrupt/resume recording demonstrates robustness.

---

## Decision framework

**Pick the minimum budget ($5) if:**
- You're confident the model labels well on short runs
- Time is more important than comprehensive evidence
- You just want to pass the assignment

**Pick the comprehensive budget ($90–150) if:**
- You want high confidence in the recommendation
- You want to measure quality at different scales
- You want to demonstrate interrupt/resume and error recovery
- You have time for a long run (can submit while Batch API processes overnight)

---

## Money-saving tips

1. **Use Batch API** (–50%): The full run on Batch API is ~$55–70 instead of $100–130. It's async (1–24 hours) but half the price.
2. **Start with 500-review run** and skip 10k if label quality looks good. Saves $10–15.
3. **Run the pilot first**. It measures real costs and lets you adjust your budget before the big run.

---

## What the budget field does

When you run:
```bash
uv run python -m pipeline run --budget 5.00 ...
```

The pipeline:
1. **Reserves** worst-case cost before each batch (uncached input + max output tokens)
2. **Stops admitting work** when: `spent + reserved + next_batch > budget`
3. **Gracefully saves** all progress when it hits the cap
4. **Allows resume** with the same budget (completed work is cached and free on re-run)

So `--budget 5.00` means "stop when you've spent $5, but save your work so you can resume."

---

## After the run

Once you've chosen a budget and run the pipeline:

1. **Check the costs** in `runs/<run-id>/run_summary.json`:
   ```bash
   jq '.spend_usd_actual' runs/<run-id>/run_summary.json
   ```

2. **Evaluate label quality** against your 50 golden labels (if you've labeled them):
   ```bash
   uv run python -m pipeline eval-golden --run-id checkpoint500
   cat evals/golden/golden_summary.json
   ```

3. **Adjust for the next run** if needed. If accuracy is <80%, I can tune the prompts before the full run.

---

**Recommendation for your situation:** Start with **$5 budget for the 500-review run**. That gives you:
- Real label quality measurement (500 rows)
- A grading-ready folder
- ~$3–5 actual spend
- Time to decide if you want the full $55–130 run

Then, if you want the full production run, budget $80 (Batch API) and submit the full-run grading/ folder instead.
