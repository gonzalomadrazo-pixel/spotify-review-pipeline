#!/usr/bin/env python3
"""Quick setup checker and guide."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def check_env():
    """Check if .env exists and has ANTHROPIC_API_KEY."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        print("❌ .env not found")
        print("   → Run: cp .env.example .env")
        print("   → Then edit .env and add your ANTHROPIC_API_KEY")
        return False
    content = env_path.read_text()
    if "ANTHROPIC_API_KEY=" not in content or content.count("ANTHROPIC_API_KEY=sk-") == 0:
        print("❌ .env missing valid ANTHROPIC_API_KEY")
        print("   → Edit .env and add: ANTHROPIC_API_KEY=sk-...")
        return False
    print("✅ API key configured")
    return True


def check_data():
    """Check if data files exist."""
    files = {
        "cost_100.csv": "c884ac3b9be5066995d5063f96ad9af6e5e082975788c1684c4f6b6ea661dd0e",
        "checkpoint_500.csv": "a94e31663ee7b7eaa77e23b7a8425b530cc0c866ed5e714953172a0afef6a12f",
        "analysis_10000.csv": "eaa62ca6d44d717302904a9c922e99b0b4584d174309cb45295ecbc2ba2a91b5",
        "golden_50_to_label.csv": "1a125c3e509f58b0246ba16d0ea332675a53ffadb1928be4338a0bd7053a11c7",
        "spotify_reviews_18months.csv": "1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6",
    }
    data_dir = ROOT / "data" / "raw"
    missing = []
    for fname in files:
        path = data_dir / fname
        if not path.exists():
            missing.append(fname)
    if missing:
        print(f"❌ Missing {len(missing)} data file(s):")
        for f in missing:
            print(f"   → {f}")
        print("   → Download from bCourses and unzip into data/raw/")
        return False
    print("✅ All data files present")
    return True


def check_golden():
    """Check if golden labels exist."""
    path = ROOT / "evals" / "golden_50_labeled.csv"
    if not path.exists():
        print("⚠️  Golden labels not yet created")
        print("   → Open evals/golden_labeler.html in your browser")
        print("   → Label all 50 reviews, then Export CSV as evals/golden_50_labeled.csv")
        return False
    print("✅ Golden labels present")
    return True


def main():
    print("\n📋 Setup Checklist\n")

    checks = [
        ("API key", check_env),
        ("Data files", check_data),
        ("Golden labels", check_golden),
    ]

    results = []
    for name, check in checks:
        results.append(check())
        print()

    if all(results[:2]):
        print("✨ Ready to start!")
        print("\nNext steps:")
        print("  1. uv run python -m pipeline smoke          (verify key works)")
        print("  2. uv run python cost/calculator.py run-pilot --budget 1.00  (measure costs)")
        print("  3. uv run python -m pipeline run --run-id checkpoint500 --input data/raw/checkpoint_500.csv --budget 5.00")
        if results[2]:
            print("  4. uv run python -m pipeline eval-golden --run-id checkpoint500")
        else:
            print("  4. [After labeling golden:] uv run python -m pipeline eval-golden --run-id checkpoint500")
        print("\nSee SETUP_AND_RUN.md for the full workflow.")
        return 0
    else:
        print("❌ Setup incomplete. Fix the issues above and run again.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
