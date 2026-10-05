"""Code-owned aggregates for the memo: product-area rollup, comparable-period trend and the FACTS table.

Every number the memo may use is computed here from saved records + membership and given a stable
fact ID. Issue-level facts use only the contract's supported metrics and become claims.csv rows;
other facts (area rollups, trend shares, coverage) go to claims_extra.csv with their formula, and
`check_claims` recomputes all of them from the saved files without any model call.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal

from .rank import mean_string

AREAS = {"access": "access", "usability": "usability", "playback": "playback", "billing": "billing_support",
         "support": "billing_support", "downloads": "downloads", "catalog": "catalog", "other": "other"}
AREA_ORDER = ["access", "usability", "playback", "billing_support", "downloads", "catalog", "other"]
PERIODS = {"jun_oct_2022": ("2022-06", "2022-10"), "jun_oct_2023": ("2023-06", "2023-10")}
ISSUE_METRICS = ("complaint_count", "severity_sum", "mean_severity", "priority_score")


def fmt_int(n) -> str:
    return f"{int(n):,}"


def fmt_pct(num, den) -> str:
    if not den:
        return "n/a"
    return str((Decimal(num) * 100 / Decimal(den)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) + "%"


def fmt_mean2(total, n) -> str:
    return str((Decimal(total) / Decimal(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def area_rollup(completed: list[dict]) -> list[dict]:
    agg = defaultdict(Counter)
    total_complaints = 0
    for r in completed:
        if r["intent"] not in ("complaint", "cancellation"):
            continue
        a = AREAS[r["topic"]]
        agg[a]["complaint_count"] += 1
        agg[a]["severity_sum"] += r["severity"]
        agg[a]["severe_count"] += r["severity"] >= 4
        agg[a]["cancellation_count"] += r["intent"] == "cancellation"
        total_complaints += 1
    rows = []
    for a in AREA_ORDER:
        c = agg.get(a, Counter())
        n = c["complaint_count"]
        rows.append({"area": a, "complaint_count": n, "severity_sum": c["severity_sum"],
                     "mean_severity": mean_string(c["severity_sum"], n) if n else "",
                     "share_of_complaints": fmt_pct(n, total_complaints), "severe_count_sev4plus": c["severe_count"],
                     "cancellation_count": c["cancellation_count"], "total_complaints_all_areas": total_complaints})
    return rows


def monthly_trend(all_rows: list[dict], completed_by_id: dict) -> list[dict]:
    """Per month: total reviews (denominator) and complaint/cancellation counts per area."""
    months = defaultdict(Counter)
    for r in all_rows:
        m = r["review_timestamp"][:7]
        months[m]["reviews"] += 1
        c = completed_by_id.get(r["review_id"])
        if c and c["intent"] in ("complaint", "cancellation"):
            months[m]["complaints"] += 1
            months[m][AREAS[c["topic"]]] += 1
    out = []
    for m in sorted(months):
        row = {"month": m, "reviews": months[m]["reviews"], "complaints": months[m]["complaints"]}
        for a in AREA_ORDER:
            row[a] = months[m][a]
        out.append(row)
    return out


def period_shares(trend: list[dict]) -> dict:
    res = {}
    for name, (lo, hi) in PERIODS.items():
        rows = [t for t in trend if lo <= t["month"] <= hi]
        reviews = sum(t["reviews"] for t in rows)
        res[name] = {"reviews": reviews, **{a: sum(t[a] for t in rows) for a in AREA_ORDER}}
    return res


def build_facts(ranking: list[dict], issues: list[dict], areas: list[dict], periods: dict, coverage: dict,
                top_n: int) -> list[dict]:
    facts = []

    def add(scope, subject, metric, value, display, formula):
        facts.append({"fact_id": f"F{len(facts) + 1:02d}", "scope": scope, "subject": subject, "metric": metric,
                      "value": str(value), "display": display, "formula": formula})

    add("corpus", "all", "source_rows", coverage["rows"], fmt_int(coverage["rows"]), "rows in input CSV")
    add("corpus", "all", "completed_classifications", coverage["completed"], fmt_int(coverage["completed"]),
        "records with status=completed")
    add("corpus", "all", "quarantined_records", coverage["quarantined"], fmt_int(coverage["quarantined"]),
        "records with status=quarantined (all reasons)")
    add("corpus", "all", "pending_records", coverage["pending"], fmt_int(coverage["pending"]), "records still pending")
    add("corpus", "all", "complaint_or_cancellation_records", coverage["complaints"], fmt_int(coverage["complaints"]),
        "completed records with intent in {complaint, cancellation}")
    add("corpus", "all", "cancellation_records", coverage["cancellations"], fmt_int(coverage["cancellations"]),
        "completed records with intent = cancellation")
    for a in areas:
        if not a["complaint_count"]:
            continue
        s = a["area"]
        add("area", s, "complaint_count", a["complaint_count"], fmt_int(a["complaint_count"]),
            "complaint+cancellation records whose topic maps to area")
        add("area", s, "share_of_complaints", a["share_of_complaints"], a["share_of_complaints"],
            "area complaint_count / all complaint+cancellation records, 1 dp half-up")
        add("area", s, "mean_severity", a["mean_severity"], fmt_mean2(a["severity_sum"], a["complaint_count"]),
            "area severity_sum / complaint_count (display 2 dp half-up)")
        add("area", s, "severity_sum", a["severity_sum"], fmt_int(a["severity_sum"]), "sum of member severities")
        add("area", s, "severe_count_sev4plus", a["severe_count_sev4plus"], fmt_int(a["severe_count_sev4plus"]),
            "complaints with severity >= 4")
        add("area", s, "cancellation_count", a["cancellation_count"], fmt_int(a["cancellation_count"]),
            "records with intent = cancellation")
    for row in ranking[:top_n]:
        iid = row["issue_id"]
        add("issue", iid, "rank", row["rank"], str(row["rank"]), "baseline rank (desc priority_score, asc issue_id)")
        for m in ISSUE_METRICS:
            v = row[m]
            disp = fmt_mean2(row["severity_sum"], row["complaint_count"]) if m == "mean_severity" else fmt_int(v)
            add("issue", iid, m, v, disp, "contract baseline: " + m)
    for name, p in periods.items():
        add("trend", name, "reviews", p["reviews"], fmt_int(p["reviews"]), f"all reviews in {name} (denominator)")
        for a in ("access", "usability", "playback", "billing_support"):
            add("trend", f"{name}:{a}", "complaints_per_100_reviews", fmt_pct(p[a], p["reviews"]),
                fmt_pct(p[a], p["reviews"]), f"{a} complaint records / all reviews in {name}, as %")
    return facts


def check_claims(facts: list[dict], ranking: list[dict], areas: list[dict], periods: dict, coverage: dict,
                 top_n: int) -> list[str]:
    """Recompute every fact from the given aggregates; return mismatches (empty list = all claims verified)."""
    expected = {(f["scope"], f["subject"], f["metric"]): f["value"]
                for f in build_facts(ranking, [], areas, periods, coverage, top_n)}
    errors = []
    for f in facts:
        key = (f["scope"], f["subject"], f["metric"])
        if expected.get(key) != f["value"]:
            errors.append(f"{f['fact_id']} {key}: saved {f['value']} != recomputed {expected.get(key)}")
    return errors
