#!/usr/bin/env python3
"""Main CLI for the Spotify review pipeline."""
import sys
import json
import argparse
from pathlib import Path

from src.ingest import ingest_full
from src.orchestrate import Pipeline
from cost.calculator import CostCalculator


def main():
    parser = argparse.ArgumentParser(
        description="Spotify review pipeline: ingest, classify, group, rank, recommend."
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest CSV into database")
    ingest_parser.add_argument("csv", help="Path to CSV file")
    ingest_parser.add_argument("--db", default="data/pipeline.db", help="Database path")

    # Enrich command
    enrich_parser = subparsers.add_parser("enrich", help="Run enrichment stage")
    enrich_parser.add_argument("--db", default="data/pipeline.db", help="Database path")
    enrich_parser.add_argument("--batch-size", type=int, default=50, help="Reviews per batch")
    enrich_parser.add_argument("--max-batches", type=int, help="Max batches to run")
    enrich_parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="Model ID")

    # Verify command
    verify_parser = subparsers.add_parser("verify", help="Run verification stage")
    verify_parser.add_argument("--db", default="data/pipeline.db", help="Database path")
    verify_parser.add_argument("--sample-size", type=int, default=10, help="Sample size")

    # Rank command
    rank_parser = subparsers.add_parser("rank", help="Compute ranking")
    rank_parser.add_argument("--db", default="data/pipeline.db", help="Database path")

    # Full-run command (for later, when testing is done)
    full_parser = subparsers.add_parser("run-full", help="Run full pipeline")
    full_parser.add_argument("csv", help="Path to CSV file")
    full_parser.add_argument("--db", default="data/pipeline.db", help="Database path")

    args = parser.parse_args()

    if args.command == "ingest":
        print("Ingesting CSV...")
        result = ingest_full(args.csv, args.db)
        print(json.dumps(result, indent=2))

    elif args.command == "enrich":
        print("Running enrichment...")
        config = {"model": args.model}
        pipe = Pipeline(args.db, {"enricher": config})
        result = pipe.enrich(batch_size=args.batch_size, max_batches=args.max_batches)
        print(json.dumps(result, indent=2))
        pipe.close()

    elif args.command == "verify":
        print("Running verification...")
        pipe = Pipeline(args.db)
        result = pipe.verify(sample_size=args.sample_size)
        print(json.dumps(result, indent=2))
        pipe.close()

    elif args.command == "rank":
        print("Computing ranking...")
        pipe = Pipeline(args.db)
        result = pipe.rank()
        print(json.dumps(result, indent=2))
        pipe.close()

    elif args.command == "run-full":
        print("Running full pipeline...")
        # TODO: implement full run
        print("Not yet implemented.")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
