# Spotify Review Analysis Pipeline

Classify, group and rank 660,622 Google Play reviews of the Spotify Android app to guide product investment decisions. The pipeline is structured as offline-first stages that exchange saved artifacts, with optional model calls for enrichment, verification and memo writing.

## Overview

**Six stages:**

1. **Ingest** (code): Read the 97.4 MB CSV, profile it, create state database.
2. **Enrich** (model): Classify each review's topic, intent, sentiment and severity using structured labels. Cache exact-text reuse.
3. **Verify** (model): Independent re-labelling of a random sample to measure agreement.
4. **Group** (code): Aggregate complaints by subtopic; code assigns membership. Model suggests names and coherence.
5. **Rank** (code): Sort issues by complaint_count × mean_severity.
6. **Recommend** (model): Write decision memo citing issue IDs and numbers.

**Why this order:** Inexpensive preparation (read, deduplicate, cache lookup) before classification, fixed labels for repeatable decisions, aggregation before memo. Supports resume after interruption, with saved checkpoints and no re-labelling of completed IDs.

## Setup

### 1. Environment

```bash
# Clone or navigate to the repo
cd ~/Claude/spotify-review-pipeline

# Install dependencies (uses uv)
uv sync

# Copy .env.example to .env and add your API key
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

### 2. Prepare the dataset

Download the Spotify dataset from [Google Drive](https://drive.google.com/file/d/1P0rUoAS_wVjp3BYKqXMEyD4u0uJP1Bvf/view) or use the Kaggle source and the provided `prepare_dataset.py`:

```bash
# If using the raw Kaggle archive
python data/raw/prepare_dataset.py /path/to/archive.zip --output data
```

The course dataset files (`cost_100.csv`, `checkpoint_500.csv`, `analysis_10000.csv`, `golden_50_to_label.csv`, and the full `spotify_reviews_18months.csv`) are already provided in `data/raw/`.

### 3. Ingest the dataset

```bash
# Ingest the full 660k review CSV (required for the full run)
uv run python main.py ingest data/raw/spotify_reviews_18months.csv

# Or start with a smaller development file
uv run python main.py ingest data/raw/cost_100.csv --db data/test.db
```

This creates a DuckDB database with tables for ingestion, enrichment, verification, membership and API call logging.

## Workflow

### 100-Review Pilot (Measured Cost & Runtime)

Required before scaling. Measures actual API throughput and token usage on the first 100 reviews, with projections for the full run.

```bash
# Ingest the 100-review pilot
uv run python main.py ingest data/raw/cost_100.csv --db data/pilot_100.db

# Run enrichment (this calls the API)
export ANTHROPIC_API_KEY=sk-ant-...
uv run python main.py enrich --db data/pilot_100.db --model claude-haiku-4-5-20251001

# Verify a sample (independent re-labelling)
uv run python main.py verify --db data/pilot_100.db --sample-size 10

# Compute ranking from completed enrichment
uv run python main.py rank --db data/pilot_100.db
```

Check `cost/report.md` for measured costs and time, and `cost/pilot_records.jsonl` for all 100 results with IDs.

### Projected Full Run

After the 100-review pilot:

```bash
# Ingest the full 660k CSV
uv run python main.py ingest data/raw/spotify_reviews_18months.csv

# Run enrichment in batches (resume on timeout or budget limit)
# This runs asynchronously and saves progress after each batch
uv run python main.py enrich --db data/pipeline.db --batch-size 50

# Verify a fresh sample (no cost to ingestion, minimal verification cost)
uv run python main.py verify --db data/pipeline.db --sample-size 50

# Rank issues by complaint severity
uv run python main.py rank --db data/pipeline.db

# (TODO) Write memo
# uv run python main.py memo --db data/pipeline.db
```

## Labels and Schema

See [`labels/LABEL_GUIDE.md`](labels/LABEL_GUIDE.md) for the full labeling rubric. 

**Common labels** (from the assignment's GRADING_CONTRACT):

- **topic**: access, usability, playback, downloads, catalog, billing, support, other
- **intent**: cancellation, complaint, request, praise, unclear
- **severity**: 1–5 scale (1 = no problem, 5 = explicit serious harm)
- **subtopic**: specific category under the topic (e.g., `playback.crash_or_wont_open`, `billing.free_tier_restrictions`)

**Output schema** for enriched records: `{review_id, source_sha256, status, topic, subtopic, intent, sentiment, severity, entities, evidence_quote, needs_review, label_config, cache_source_id}`

## Stages and Code

- **`src/ingest.py`** — Read CSV, create database, profile.
- **`src/state.py`** — DuckDB state store with enrichment, verification, membership, API call logging.
- **`src/stages.py`** — Stage base class and stubs for Enricher, Verifier, Grouper, Ranker, Recommender. (Model implementations added when API integration is ready.)
- **`src/orchestrate.py`** — Pipeline orchestrator: ingest, enrich, verify, rank.
- **`cost/calculator.py`** — Cost calculator: record API calls, measure throughput, project full run, save report.
- **`prompts/enrich_v1.md`** — Enrichment prompt (security notes, label definitions, examples).
- **`prompts/verify_v1.md`** — Verification prompt (independent re-labelling).
- **`prompts/group_v1.md`** — Grouping prompt (issue names and coherence).
- **`prompts/memo_v1.md`** — Recommendation memo prompt.

## Resume After Interruption

Saved checkpoints allow the pipeline to resume where it stopped, without re-enriching completed reviews.

```bash
# Get completed IDs before interrupt
python -c "
from src.state import StateStore
store = StateStore('data/pipeline.db')
ids = store.get_completed_ids()
print(json.dumps({'completed_ids': sorted(ids)}))" > grading/checkpoint_before.json

# ... (interrupt or wait)

# Resume enrichment
uv run python main.py enrich --db data/pipeline.db

# Get completed IDs after resume
python -c "..." > grading/checkpoint_after.json
```

## Verification and Evaluation

See [`labels/LABEL_GUIDE.md`](labels/LABEL_GUIDE.md) for worked examples and decision rules. Golden-set labels (the hand-labeled 50) are kept separate from the development files and model inputs.

- **Per-field evaluation**: topic, intent, severity agreement on a held-out sample.
- **Confusion matrix**: where predictions diverge from hand labels.
- **Injection tests**: verify the model rejects instruction injection in review text.

Run the provided checker:

```bash
python data/raw/check_submission.py profile --full data/raw/spotify_reviews_18months.csv --out grading/ingestion.json
python data/raw/check_submission.py check --reference grading/reference.json --submission grading --out grading/self-check.json
```

## Cost and Runtime

### Measured Pilot (100 reviews)

The calculator records every API call and measures wall-clock time. Results in `cost/`:

- **`pilot_records.jsonl`**: One line per review with enriched labels and cache status.
- **`pilot_calls.jsonl`**: Every API call: model, tokens, outcome, timing.
- **`rates.csv`**: Editable pricing (Anthropic, OpenAI, etc.) with source links.
- **`usage.csv`**: Aggregated token usage by model and stage.
- **`report.md`**: Cost summary, throughput, full-run projection.
- **`replay.sh`**: Offline command to recompute costs from saved usage without API calls.

### Illustration (Oct 2026 rates)

660,609 nonempty reviews, 484,189 distinct texts (exact-text caching):

| Model | Tier | Input $/1M | Output $/1M | Est. tokens/review | Est. cost |
|---|---|---|---|---|---|
| Claude Haiku 4.5 | Standard API | $1.00 | $5.00 | 100 in, 150 out | ~$82 |
| Claude Haiku 4.5 | Batch API (50% off) | $0.50 | $2.50 | (same) | ~$41 |

Actual costs depend on prompt size, model choice, batch vs. standard API, retry rates, and whether your account runs on a subscription with fixed fees (reported as $0 marginal cost) or pay-as-you-go (billed by token).

## Architecture

```
CSV (660,622 rows)
    ↓
Ingest (code) → DuckDB database
    ↓
Enrich (model + code)
    ├─ Call model on batches ≤ 50 reviews
    ├─ Cache exact-text reuse
    ├─ Validate output schema
    └─ Save enriched.jsonl / quarantine.jsonl
    ↓
Verify (model, independent sample)
    ├─ Sample random reviews
    ├─ Re-label without seeing prior labels
    └─ Compare agreement (topic, intent, severity)
    ↓
Group (code + optional model)
    ├─ Aggregate by topic.subtopic
    ├─ Code counts and sums severity
    └─ Model names issues (optional)
    ↓
Rank (code)
    ├─ Filter: complaint + cancellation only
    ├─ Score: complaint_count × mean_severity
    └─ Sort by score, then issue ID
    ↓
Recommend (model)
    ├─ Read saved aggregates only
    ├─ Write memo citing issue IDs and numbers
    └─ memo.md
```

## Testing

Run the ingestion test:

```bash
cd /Users/gonzalomadrazo/Claude/spotify-review-pipeline
uv run python -m src.ingest data/raw/cost_100.csv data/test.db
# Expected output: {"ingested": 100, "duplicate_ids": 0, ...}
```

## References

- **GRADING_CONTRACT.md** — Label definitions, common errors, export format.
- **COST_CALCULATOR.md** — Cost calculator requirements and specifications.
- **labels/LABEL_GUIDE.md** — Detailed labeling rules with examples.
- **Kaggle dataset**: [BwandoWando / 3.4 Million Spotify Google Store Reviews](https://www.kaggle.com/datasets/bwandowando/3-4-million-spotify-google-store-reviews)

## Due Date

October 13, 2026, 11:59 pm Pacific Time.
