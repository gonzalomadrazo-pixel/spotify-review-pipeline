"""Shared helpers: paths, config, taxonomy, label_config, rates and atomic file writes."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file + rename so readers never see a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_json(path: Path, value) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_jsonl(path: Path, rows) -> int:
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=False, allow_nan=False) for r in rows]
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


def write_csv(path: Path, fieldnames, rows) -> int:
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(fieldnames), lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    n = 0
    for r in rows:
        w.writerow(r)
        n += 1
    atomic_write_text(path, buf.getvalue())
    return n


def read_jsonl(path: Path):
    import gzip
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def append_jsonl(path: Path, row) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        f.flush()


# ---------------------------------------------------------------- taxonomy

def load_taxonomy() -> dict:
    return json.loads((ROOT / "labels" / "taxonomy.json").read_text(encoding="utf-8"))


TAXONOMY = load_taxonomy()
TOPICS = tuple(TAXONOMY["topics"].keys())
INTENTS = tuple(TAXONOMY["intent_precedence"])
SUBTOPICS = tuple(s for t in TAXONOMY["topics"].values() for s in t["subtopics"])
SUB_TO_TOPIC = {s: s.split(".", 1)[0] for s in SUBTOPICS}
SENTIMENT_MAP = {int(k): v for k, v in TAXONOMY["sentiment_levels"].items()}
REVIEW_REASONS = tuple(TAXONOMY["needs_review_reasons"])
assert set(SUB_TO_TOPIC.values()) == set(TOPICS)


def render_taxonomy_block() -> str:
    lines = []
    for topic, spec in TAXONOMY["topics"].items():
        lines.append(f"### {topic}: {spec['definition']}")
        for code, desc in spec["subtopics"].items():
            lines.append(f"- {code}: {desc}")
        lines.append("")
    return "\n".join(lines).strip()


def render_topics_block() -> str:
    return "\n".join(f"- {t}: {spec['definition']}" for t, spec in TAXONOMY["topics"].items())


# ---------------------------------------------------------------- config

def load_config(path: Path | None = None) -> dict:
    path = Path(path) if path else ROOT / "config" / "pipeline.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["_path"] = str(path)
    return cfg


def render_prompt(prompt_file: str) -> str:
    text = (ROOT / prompt_file).read_text(encoding="utf-8")
    return text.replace("{TAXONOMY}", render_taxonomy_block()).replace("{TOPICS}", render_topics_block())


def role_fingerprint(role_cfg: dict, prompt_text: str, schema: dict | None) -> str:
    """Short hash over everything that changes a model's answer: prompt, schema, model, sampling."""
    material = {
        "prompt": prompt_text,
        "schema": schema,
        "model": role_cfg.get("model"),
        "temperature": role_cfg.get("temperature"),
        "effort": role_cfg.get("effort"),
        "taxonomy": TAXONOMY["version"],
    }
    return sha256_text(canonical_json(material))[:10]


def label_config_string(model: str, prompt_version: str, schema_version: str, fingerprint: str) -> str:
    return f"{model}+{prompt_version}+{schema_version}+{fingerprint}"


# ---------------------------------------------------------------- rates

def load_rates(path: Path | None = None, multiplier: float = 1.0) -> dict:
    """Return {(model, tier, billing_item): price_per_token} from the editable rates CSV."""
    path = Path(path) if path else ROOT / "config" / "rates.csv"
    rates = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rates[(row["model"], row["tier"], row["billing_item"])] = float(row["price_usd_per_unit"]) * multiplier
    return rates


BILLING_ITEMS = ("input_uncached", "input_cache_write_5m", "input_cache_read", "output")


def usage_cost(rates: dict, model: str, tier: str, usage: dict) -> tuple[float, dict]:
    """item_cost = billed_units x price_per_unit, over mutually exclusive token categories.

    Anthropic reports input_tokens (uncached, after the last cache breakpoint),
    cache_creation_input_tokens and cache_read_input_tokens separately, so no
    subtraction is needed. Output tokens already include any thinking tokens.
    """
    units = {
        "input_uncached": int(usage.get("input_tokens") or 0),
        "input_cache_write_5m": int(usage.get("cache_creation_input_tokens") or 0),
        "input_cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
    }
    items = {}
    for item, n in units.items():
        price = rates.get((model, tier, item))
        if price is None:
            raise KeyError(f"No rate for {model}/{tier}/{item}; add it to config/rates.csv")
        items[item] = n * price
    return sum(items.values()), items


class Timer:
    def __init__(self):
        self.start = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self.start
