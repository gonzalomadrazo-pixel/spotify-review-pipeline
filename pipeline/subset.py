"""Build the declared analysis subset (code only, no model).

Extends the course's own deterministic sampler (data/raw/prepare_dataset.py): rank every unique,
nonempty, valid-rating record by SHA-256(seed + ":" + review_id) and keep the lowest N. With the course
seed, the first 50 ranks are the golden set and the next 10,000 are analysis_10000.csv (which nests
checkpoint_500.csv and cost_100.csv), so all fixed course files are inside the subset. All empty-text
rows are added so they are explicitly quarantined. Rows keep their original field strings and source order.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from .common import FIELDS, ROOT, now_iso, sha256_file, write_json
from .vendor import check_submission as checker

COURSE_SEED = "berkeley-fall-2026-assignment-5-v1"


def rank_key(review_id: str, seed: str = COURSE_SEED) -> int:
    return int.from_bytes(hashlib.sha256((seed + ":" + review_id).encode()).digest(), "big")


def build_subset(full: Path, out: Path, n_nonempty: int, seed: str = COURSE_SEED) -> dict:
    rows = list(checker.csv_rows(full))
    seen, eligible, empty_idx = set(), [], []
    for idx, r in enumerate(rows):
        rid = r["review_id"]
        dup = rid in seen
        seen.add(rid)
        if not r["review_text"].strip():
            empty_idx.append(idx)
            continue
        if dup or not rid or r["review_rating"] not in {"1", "2", "3", "4", "5"}:
            continue
        eligible.append((rank_key(rid, seed), rid, idx))
    if len(eligible) < n_nonempty:
        raise SystemExit(f"Only {len(eligible)} eligible rows; cannot sample {n_nonempty}.")
    eligible.sort()
    chosen = sorted([idx for _, _, idx in eligible[:n_nonempty]] + empty_idx)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(FIELDS), lineterminator="\n")
        w.writeheader()
        for idx in chosen:
            w.writerow({k: rows[idx][k] for k in FIELDS})
    picked = [rows[i] for i in chosen]
    texts = Counter(r["review_text"] for r in picked if r["review_text"].strip())
    chosen_ids = {r["review_id"] for r in picked}
    nested = {}
    raw = ROOT / "data" / "raw"
    for name in ("golden_50_to_label.csv", "analysis_10000.csv", "checkpoint_500.csv", "cost_100.csv"):
        p = raw / name
        if p.exists():
            ids = [r["review_id"] for r in checker.csv_rows(p)]
            nested[name] = {"rows": len(ids), "inside_subset": sum(i in chosen_ids for i in ids)}
    months = Counter(r["review_timestamp"][:7] for r in picked)
    manifest = {
        "generated_at": now_iso(),
        "full_input": {"path": str(full), "sha256": sha256_file(full), "rows": len(rows)},
        "subset": {"path": str(out), "sha256": sha256_file(out), "rows": len(picked),
                   "nonempty_sampled": n_nonempty, "empty_text_rows_added": len(empty_idx),
                   "distinct_nonempty_texts": len(texts),
                   "rows_reusable_by_exact_text_cache": sum(texts.values()) - len(texts)},
        "method": {"seed": seed,
                   "rule": "Lowest SHA-256(seed + ':' + review_id) among unique nonempty valid-rating records "
                           "(the course sampler's rule and seed, extended to N), plus every empty-text row; "
                           "written in source order with original field strings."},
        "nested_course_files": nested,
        "reviews_by_month": dict(sorted(months.items())),
    }
    write_json(out.with_name(out.stem + "_manifest.json"), manifest)
    return manifest


if __name__ == "__main__":  # pragma: no cover
    import sys
    print(json.dumps(build_subset(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])), indent=2))
