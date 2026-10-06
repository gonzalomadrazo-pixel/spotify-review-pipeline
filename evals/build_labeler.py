"""Build evals/golden_labeler.html from data/raw/golden_50_to_label.csv (offline, no model).

The page embeds only the 50 review texts and the shared label definitions; every label starts blank
and must be chosen by a person. Progress autosaves in the browser; "Export CSV" downloads
golden_50_labeled.csv - save it as evals/golden_50_labeled.csv.

Usage: uv run python evals/build_labeler.py
"""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
rows = list(csv.DictReader(open(ROOT / "data/raw/golden_50_to_label.csv", encoding="utf-8-sig", newline="")))
tax = json.loads((ROOT / "labels/taxonomy.json").read_text())
data = [{k: r[k] for k in ("review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp")}
        for r in rows]
template = (ROOT / "evals/labeler_template.html").read_text(encoding="utf-8")
page = (template.replace("/*__DATA__*/[]", json.dumps(data, ensure_ascii=False))
        .replace("/*__TAX__*/{}", json.dumps(tax, ensure_ascii=False)))
(ROOT / "evals/golden_labeler.html").write_text(page, encoding="utf-8")
print(f"wrote evals/golden_labeler.html with {len(data)} reviews")
