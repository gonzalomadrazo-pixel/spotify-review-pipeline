"""Golden-set evaluation (code only): human labels vs. saved predictions, per field and per case.

Human labels come from evals/golden_50_labeled.csv (exported by evals/golden_labeler.html). They are
never sent to a model. Optional alt_* columns list additional acceptable labels for ambiguous cases.
Predeclared tolerances: sentiment |diff| <= 0.5 counts as agreement; severity reports exact agreement,
within-1 agreement and mean absolute error. Missing/quarantined predictions count as incorrect.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from .common import INTENTS, TOPICS, write_csv, write_json
from .vendor import check_submission as checker

SENTIMENT_TOLERANCE = 0.5


def _list(v):
    return [x.strip() for x in (v or "").replace(",", ";").split(";") if x.strip()]


def load_human(path: Path) -> dict:
    rows = {}
    for r in checker.csv_rows(path):
        if not (r.get("topic") or "").strip():
            raise SystemExit(f"Unlabeled golden row {r['review_id']}: label all 50 before evaluating.")
        rows[r["review_id"]] = r
    return rows


def evaluate(human_path: Path, predictions: dict, out_dir: Path) -> dict:
    human = load_human(human_path)
    cases, conf_topic, conf_intent = [], defaultdict(Counter), defaultdict(Counter)
    tot = Counter()
    sev_err, sent_err = [], []
    for rid, h in human.items():
        p = predictions.get(rid)
        acc_topic = [h["topic"].strip()] + [t for t in _list(h.get("alt_topic")) if t in TOPICS]
        acc_intent = [h["intent"].strip()] + [t for t in _list(h.get("alt_intent")) if t in INTENTS]
        acc_sev = [int(h["severity"])] + [int(s) for s in _list(h.get("alt_severity")) if s.isdigit()]
        ambiguous = (h.get("ambiguous") or "").strip().lower() in ("true", "1", "yes") or len(acc_topic) > 1 \
            or len(acc_intent) > 1 or len(acc_sev) > 1
        case = {"review_id": rid, "text": h["review_text"][:240], "human_topic": "|".join(acc_topic),
                "human_intent": "|".join(acc_intent), "human_severity": "|".join(map(str, acc_sev)),
                "human_sentiment": h.get("sentiment"), "human_needs_review": h.get("needs_review"),
                "ambiguous": ambiguous, "prediction_status": p["status"] if p else "missing"}
        tot["cases"] += 1
        tot["ambiguous"] += ambiguous
        if not p or p["status"] != "completed":
            for f in ("topic", "intent", "severity", "sentiment", "joint"):
                case[f + "_ok"] = False
            cases.append(case)
            tot["missing"] += 1
            continue
        case.update({"pred_topic": p["topic"], "pred_subtopic": p.get("subtopic"), "pred_intent": p["intent"],
                     "pred_severity": p["severity"], "pred_sentiment": p["sentiment"],
                     "pred_needs_review": p["needs_review"], "pred_quote": p["evidence_quote"][:160]})
        case["topic_ok"] = p["topic"] in acc_topic
        case["intent_ok"] = p["intent"] in acc_intent
        case["severity_ok"] = p["severity"] in acc_sev
        d = min(abs(p["severity"] - s) for s in acc_sev)
        case["severity_abs_err"] = d
        sev_err.append(d)
        try:
            hs = float(h["sentiment"])
            sd = abs(p["sentiment"] - hs)
            sent_err.append(sd)
            case["sentiment_abs_err"] = round(sd, 3)
            case["sentiment_ok"] = sd <= SENTIMENT_TOLERANCE
        except (TypeError, ValueError):
            case["sentiment_ok"] = None
        case["quote_is_substring"] = p["evidence_quote"] in h["review_text"]
        case["entities_all_substrings"] = all(e.lower() in h["review_text"].lower() for e in p["entities"])
        case["joint_ok"] = case["topic_ok"] and case["intent_ok"] and case["severity_ok"]
        hn = (h.get("needs_review") or "").strip().lower() in ("true", "1", "yes")
        tot["nr_tp"] += hn and p["needs_review"]
        tot["nr_fp"] += (not hn) and p["needs_review"]
        tot["nr_fn"] += hn and not p["needs_review"]
        for f in ("topic", "intent", "severity", "joint"):
            tot[f + "_ok"] += case[f + "_ok"]
        tot["sentiment_ok"] += bool(case.get("sentiment_ok"))
        tot["quote_substring"] += case["quote_is_substring"]
        tot["entities_supported"] += case["entities_all_substrings"]
        conf_topic[h["topic"].strip()][p["topic"]] += 1
        conf_intent[h["intent"].strip()][p["intent"]] += 1
        cases.append(case)
    n = tot["cases"]
    pred_n = n - tot["missing"]

    def f1_table(conf, labels):
        out = {}
        for lab in labels:
            tp = conf[lab][lab]
            support = sum(conf[lab].values())
            predicted = sum(conf[h][lab] for h in conf)
            if support or predicted:
                out[lab] = {"support": support, "predicted": predicted,
                            "precision": round(tp / predicted, 4) if predicted else None,
                            "recall": round(tp / support, 4) if support else None,
                            "f1": round(2 * tp / (support + predicted), 4) if (support + predicted) else None}
        f1s = [v["f1"] for v in out.values() if v["support"] and v["f1"] is not None]
        return out, round(sum(f1s) / len(f1s), 4) if f1s else None

    topic_cls, topic_macro = f1_table(conf_topic, TOPICS)
    intent_cls, intent_macro = f1_table(conf_intent, INTENTS)
    summary = {
        "cases": n, "ambiguous_cases": tot["ambiguous"], "missing_or_quarantined_predictions": tot["missing"],
        "agreement": {f: round(tot[f + "_ok"] / n, 4) for f in ("topic", "intent", "severity", "joint")},
        "severity_within_1": round(sum(1 for e in sev_err if e <= 1) / n, 4) if n else None,
        "severity_mae": round(sum(sev_err) / len(sev_err), 4) if sev_err else None,
        "sentiment_mae": round(sum(sent_err) / len(sent_err), 4) if sent_err else None,
        "sentiment_within_tolerance": round(tot["sentiment_ok"] / n, 4),
        "sentiment_tolerance": SENTIMENT_TOLERANCE,
        "evidence_quote_exact_substring_rate": round(tot["quote_substring"] / pred_n, 4) if pred_n else None,
        "entities_all_supported_rate": round(tot["entities_supported"] / pred_n, 4) if pred_n else None,
        "needs_review_as_prediction": {"tp": tot["nr_tp"], "fp": tot["nr_fp"], "fn": tot["nr_fn"]},
        "topic_per_class": topic_cls, "topic_macro_f1": topic_macro,
        "intent_per_class": intent_cls, "intent_macro_f1": intent_macro,
        "note": "Agreement denominators include missing/quarantined predictions. Fifty cases are a small "
                "diagnostic sample, not a population accuracy estimate.",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "golden_summary.json", summary)
    fields = list(dict.fromkeys(k for c in cases for k in c))
    write_csv(out_dir / "golden_cases.csv", fields, cases)
    write_csv(out_dir / "golden_disagreements.csv", fields, [c for c in cases if not c.get("joint_ok")])
    for name, conf, labels in (("confusion_topic.csv", conf_topic, TOPICS), ("confusion_intent.csv", conf_intent, INTENTS)):
        rows = [{"human\\pred": h, **{l: conf[h][l] for l in labels}} for h in labels if conf.get(h)]
        write_csv(out_dir / name, ["human\\pred", *labels], rows)
    return summary


def injection_check(records: dict, expected_path: Path, out_path: Path) -> dict:
    """Compare real model outputs on synthetic injection/control cases with accepted labels."""
    exp = json.loads(Path(expected_path).read_text())["cases"]
    rows, passed = [], 0
    for rid, spec in exp.items():
        p = records.get(rid)
        e = spec["expected"]
        ok = bool(p and p["status"] == "completed" and p["topic"] in e["topic"] and p["intent"] in e["intent"]
                  and p["severity"] in e["severity"])
        passed += ok
        rows.append({"review_id": rid, "purpose": spec["purpose"], "expected": e, "pass": ok,
                     "predicted": {k: p.get(k) for k in ("status", "topic", "subtopic", "intent", "severity",
                                                         "evidence_quote", "needs_review")} if p else None})
    out = {"synthetic": True, "cases": len(rows), "passed": passed, "results": rows}
    write_json(out_path, out)
    return out
