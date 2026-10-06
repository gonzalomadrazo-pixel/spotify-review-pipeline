#!/bin/bash
# Quick reference: copy-paste these commands in order
#
# Prerequisites:
# 1. cp .env.example .env && edit .env with your ANTHROPIC_API_KEY
# 2. python3 check_setup.py  (verify everything is ready)
# 3. Label the golden 50: open evals/golden_labeler.html, export as evals/golden_50_labeled.csv

set -e  # exit on first error
cd "$(dirname "$0")"

echo "🚀 Spotify Review Pipeline - Quick Start"
echo

# ============================================================================
# STEP 1: Smoke test (verify API key works)
# ============================================================================
echo "1️⃣  Running smoke test (verify key works)..."
uv run python -m pipeline smoke
echo "✅ Smoke test passed"
echo

# ============================================================================
# STEP 2: 100-review pilot (measure real costs)
# ============================================================================
echo "2️⃣  Running 100-review pilot (measure costs)..."
uv run python cost/calculator.py run-pilot --budget 1.00
echo "✅ Pilot complete. Check cost/report.md for measured vs. projected costs."
echo

# ============================================================================
# STEP 3: 500-review run (Class 7 deliverable)
# ============================================================================
echo "3️⃣  Running 500-review checkpoint (Class 7 deliverable)..."
uv run python -m pipeline run \
  --run-id checkpoint500 \
  --input data/raw/checkpoint_500.csv \
  --budget 5.00 \
  --workers 2
echo "✅ 500-review run complete."
echo "   Results: runs/checkpoint500/"
echo

# ============================================================================
# STEP 4: Evaluate against golden labels (if you've labeled them)
# ============================================================================
if [ -f "evals/golden_50_labeled.csv" ]; then
  echo "4️⃣  Evaluating against golden labels..."
  uv run python -m pipeline eval-golden --run-id checkpoint500
  echo "✅ Evaluation complete. Check evals/golden/golden_summary.json"
  echo
else
  echo "⚠️  Skipping golden evaluation (label evals/golden_50_labeled.csv first)"
  echo
fi

# ============================================================================
# STEP 5: Grading export from 500-review run
# ============================================================================
echo "5️⃣  Exporting grading folder from 500-review run..."
# Find the checkpoint files
BEFORE=$(ls runs/checkpoint500/checkpoints/inv01_*.json 2>/dev/null | head -1)
AFTER=$(ls runs/checkpoint500/checkpoints/inv02_*.json 2>/dev/null | head -1)

if [ -z "$BEFORE" ] || [ -z "$AFTER" ]; then
  echo "⚠️  Checkpoint files not found. Using defaults..."
  BEFORE="runs/checkpoint500/checkpoints/inv01_request_limit.json"
  AFTER="runs/checkpoint500/checkpoints/inv02_completed.json"
fi

uv run python -m pipeline export-grading \
  --run-id checkpoint500 \
  --input data/raw/checkpoint_500.csv \
  --out grading \
  --before "$BEFORE" \
  --after "$AFTER"
echo "✅ Grading folder ready at grading/"
echo

# ============================================================================
# OPTIONAL: Full 660k run (requires higher budget)
# ============================================================================
echo "📋 Optional: Full 660k-row production run (see BUDGET_GUIDE.md)"
echo "   Run: uv run python -m pipeline run \\"
echo "     --run-id full \\"
echo "     --input data/raw/spotify_reviews_18months.csv \\"
echo "     --budget 80 \\"
echo "     --workers 8 \\"
echo "     --mode batch"
echo

echo "🎉 Pipeline workflow complete!"
echo
echo "Next steps:"
echo "  • Check runs/checkpoint500/run_summary.json for actual costs"
echo "  • Check evals/golden/golden_summary.json for label quality"
echo "  • Decide if you want to run the full 660k-row analysis"
echo "  • Submit grading/ folder to the course"
