"""Stage 4a/5 - Group membership and baseline ranking (code only, no model).

Membership: every completed complaint/cancellation record belongs to exactly one issue, whose stable
issue_id is its subtopic code (allow_multi_issue = false). Ranking follows GRADING_CONTRACT.md exactly:
complaint_count = members, severity_sum = sum(severity), mean = severity_sum / count (6 dp, half-up),
priority_score = severity_sum; order by descending score then ascending issue_id; ranks from 1.
`rerank` regenerates ranking.csv from saved records + membership with no model call.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .common import write_csv

RANK_FIELDS = ["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"]


def mean_string(total: int, n: int) -> str:
    return str((Decimal(total) / Decimal(n)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def build_membership(records) -> list[dict]:
    """records: iterable of dicts with review_id, status, intent, subtopic. Returns membership rows."""
    out = []
    for r in records:
        if r.get("status") == "completed" and r.get("intent") in ("complaint", "cancellation"):
            out.append({"issue_id": r["subtopic"], "review_id": r["review_id"]})
    out.sort(key=lambda m: (m["issue_id"], m["review_id"]))
    return out


def compute_ranking(severity_by_id: dict, membership: list[dict]) -> list[dict]:
    members, seen = defaultdict(list), set()
    for m in membership:
        pair = (m["issue_id"], m["review_id"])
        if pair in seen:
            raise ValueError(f"duplicate membership {pair}")
        seen.add(pair)
        members[m["issue_id"]].append(int(severity_by_id[m["review_id"]]))
    rows = [{"issue_id": iid, "complaint_count": len(v), "severity_sum": sum(v),
             "mean_severity": mean_string(sum(v), len(v)), "priority_score": sum(v)} for iid, v in members.items()]
    rows.sort(key=lambda x: (-x["priority_score"], x["issue_id"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def write_rank_outputs(out_dir: Path, records: list[dict]) -> list[dict]:
    membership = build_membership(records)
    sev = {r["review_id"]: r["severity"] for r in records if r.get("status") == "completed"}
    ranking = compute_ranking(sev, membership)
    write_csv(out_dir / "membership.csv", ["issue_id", "review_id"], membership)
    write_csv(out_dir / "ranking.csv", RANK_FIELDS, ranking)
    return ranking


def rerank_from_files(records_path: Path, membership_path: Path) -> list[dict]:
    """Zero-API reproduction: read saved records (.jsonl/.jsonl.gz) and membership.csv, recompute ranking."""
    from .common import read_jsonl
    sev = {r["review_id"]: r["severity"] for r in read_jsonl(records_path) if r.get("status") == "completed"}
    with open(membership_path, encoding="utf-8", newline="") as f:
        membership = list(csv.DictReader(f))
    return compute_ranking(sev, membership)
