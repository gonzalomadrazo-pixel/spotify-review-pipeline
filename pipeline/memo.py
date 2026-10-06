"""Stage 6 - Recommend (model role: MEMO agent; code: evidence pack, claim checks, claims export).

The memo agent receives only the FACTS table, top issues (titles/summaries) and a bounded set of
quotes with review IDs. Code then checks that every number is copied from a cited fact, that cited
fact/issue/review IDs exist in what was supplied, and writes claims.csv from the cited issue facts.
A failed check triggers one revision request with the error list; persistent failures are saved and
flagged for human review rather than silently accepted.
"""

from __future__ import annotations

import json
import re
import time

from .analysis import ISSUE_METRICS
from .common import canonical_json, now_iso, render_prompt, role_fingerprint, sha256_text, write_csv, write_json
from .llm import Dispatcher, Request, Work

NUM_RE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?%?)")
FACT_RE = re.compile(r"\[(F\d{2,3})\]")
TICK_RE = re.compile(r"`([^`]+)`")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ALLOWED_BARE_NUMBERS = {"2022", "2023"}


def build_payload(facts, issues, top_n, quotes_per_issue, coverage_note) -> tuple[str, set, set]:
    lines = ["FACTS (use only these numbers; cite the ID right after each number):"]
    for f in facts:
        lines.append(f"[{f['fact_id']}] {f['scope']} {f['subject']} {f['metric']} = {f['display']}")
    lines.append("\nISSUES (baseline ranking order):")
    issue_ids, review_ids = set(), set()
    for it in issues[:top_n]:
        issue_ids.add(it["issue_id"])
        lines.append(f"- `{it['issue_id']}` (area {it['topic']}): {it.get('title', '')} - {it.get('summary', '')}")
    lines.append("\nEVIDENCE (untrusted customer quotes, review ID first):")
    for it in issues[:top_n]:
        misfit = set(it.get("misfit_ids") or [])
        n = 0
        for p in it["evidence_pack"]:
            if p["review_id"] in misfit:
                continue
            review_ids.add(p["review_id"])
            lines.append(f"- `{it['issue_id']}` `{p['review_id']}`: \"{p['quote'][:220]}\"")
            n += 1
            if n >= quotes_per_issue:
                break
    lines.append("\nSCOPE:\n" + coverage_note)
    return "\n".join(lines), issue_ids, review_ids


def check_memo(text: str, facts: list[dict], issue_ids: set, review_ids: set) -> dict:
    by_id = {f["fact_id"]: f for f in facts}
    errors, cited = [], []
    for fid in FACT_RE.findall(text):
        if fid not in by_id:
            errors.append(f"unknown fact id [{fid}]")
        else:
            cited.append(fid)
    for tok in TICK_RE.findall(text):
        tok = tok.strip()
        if UUID_RE.match(tok):
            if tok not in review_ids:
                errors.append(f"review id not in evidence pack: {tok}")
        elif re.match(r"^[a-z]+\.[a-z_]+$", tok):
            if tok not in issue_ids:
                errors.append(f"issue id not supplied: {tok}")
    # Numbers: remove backticked IDs and fact tags, then every number must equal the display value of
    # the first fact tag that follows it within the same sentence fragment.
    scrub = TICK_RE.sub(" ", text)
    for m in NUM_RE.finditer(scrub):
        num = m.group(1).rstrip(",")
        line_start = scrub.rfind("\n", 0, m.start()) + 1
        prefix = scrub[line_start:m.start()].strip()
        if re.fullmatch(r"(#+|\d+\.|[-*])?", prefix) and re.fullmatch(r"\d+", num) and scrub[m.end():m.end() + 1] == ".":
            continue  # markdown list numbering
        if scrub[max(0, m.start() - 2):m.start()] == "[F":
            continue  # part of a fact tag
        if num in ALLOWED_BARE_NUMBERS:
            continue
        tail = scrub[m.end():m.end() + 60]
        fm = FACT_RE.search(tail)
        if not fm or re.search(r"[.!?;]\s|\n", tail[:fm.start()]):
            errors.append(f"number without an adjacent fact citation: {num}")
            continue
        fact = by_id.get(fm.group(1))
        if fact and fact["display"] != num:
            errors.append(f"number {num} does not match [{fact['fact_id']}] = {fact['display']}")
    banned = [w for w in ("revenue at risk", "will reduce churn", "will improve retention", "churn rate")
              if w in text.lower()]
    errors += [f"unsupported business claim: '{w}'" for w in banned]
    return {"ok": not errors, "errors": errors, "cited_fact_ids": sorted(set(cited))}


class MemoDispatcher(Dispatcher):
    def __init__(self, ctx, client, mcfg, prompt):
        super().__init__(ctx, client, "memo", None)
        self.mcfg, self.prompt = mcfg, prompt
        self.result = None

    def build(self, work):
        return Request(role="memo", model=self.mcfg["model"], system=self.prompt, user=work.payload["user"],
                       max_tokens=self.mcfg["max_tokens"], temperature=self.mcfg.get("temperature"),
                       timeout_s=self.mcfg.get("request_timeout_s", 180))

    def on_success(self, work, attempt, request_id):
        self.result = {"text": attempt.text.strip(), "request_id": request_id, "stop_reason": attempt.stop_reason}
        return []

    def on_give_up(self, work, reason):
        self.result = {"error": reason}


def run_memo(ctx, client, facts, issues, coverage_note) -> dict:
    m = ctx.cfg["memo"]
    prompt = render_prompt(m["prompt_file"])
    fp = role_fingerprint({"model": m["model"], "temperature": m.get("temperature")}, prompt, None)
    ctx.log("stage_start", stage="memo")
    t0 = time.perf_counter()
    payload, issue_ids, review_ids = build_payload(facts, issues, m["top_issues"], m["quotes_per_issue"], coverage_note)
    key = "memo:" + sha256_text(canonical_json({"fp": fp, "payload": payload}))
    cached = ctx.store.one("SELECT value_json FROM aux_cache WHERE cache_key=?", (key,))
    attempts = []
    if cached:
        final = {**json.loads(cached[0]), "cache_hit": True}
    else:
        user = payload
        final = None
        for revision in range(m.get("max_revisions", 1) + 1):
            disp = MemoDispatcher(ctx, client, m, prompt)
            stop = disp.run([Work(key=f"memo-{revision}", payload={"user": user}, review_ids=sorted(review_ids),
                                  meta={"artifact": "facts.json+issues.json"})], 1)
            res = disp.result or {"error": "not_run:" + stop}
            if "error" in res:
                final = {"status": "failed", **res}
                break
            chk = check_memo(res["text"], facts, issue_ids, review_ids)
            attempts.append({"revision": revision, "request_id": res["request_id"], "check": chk})
            final = {"status": "checks_passed" if chk["ok"] else "checks_failed", "text": res["text"],
                     "request_id": res["request_id"], "check": chk, "revisions": revision}
            if chk["ok"]:
                break
            user = (payload + "\n\nYour previous draft failed automated checks. Fix every problem below and return "
                    "the full corrected memo.\nPROBLEMS:\n- " + "\n- ".join(chk["errors"][:25]) +
                    "\n\nPREVIOUS DRAFT:\n" + res["text"])
        if final.get("text"):
            with ctx.store.tx() as db:
                db.execute("INSERT OR REPLACE INTO aux_cache VALUES (?,?,?,?,?,?)",
                           (key, "memo", json.dumps({**final, "attempts": attempts}, ensure_ascii=False),
                            final.get("request_id"), ctx.run_id, now_iso()))
    text = final.get("text", "")
    chk = final.get("check") or check_memo(text, facts, issue_ids, review_ids)
    by_id = {f["fact_id"]: f for f in facts}
    claims, extra = [], []
    for fid in chk["cited_fact_ids"]:
        f = by_id[fid]
        if f["scope"] == "issue" and f["metric"] in ISSUE_METRICS:
            claims.append({"claim_id": fid, "issue_id": f["subject"], "metric": f["metric"], "value": f["value"]})
        else:
            extra.append({"claim_id": fid, "scope": f["scope"], "subject": f["subject"], "metric": f["metric"],
                          "value": f["value"], "display": f["display"], "formula": f["formula"]})
    header = (f"<!-- Generated by the memo agent ({m['model']}, {m['prompt_version']}); status: {final.get('status')}. "
              f"Numbers cite fact/claim IDs in facts.json, claims.csv and claims_extra.csv. -->\n\n")
    (ctx.run_dir / "memo.md").write_text(header + text + "\n", encoding="utf-8")
    write_csv(ctx.run_dir / "claims.csv", ["claim_id", "issue_id", "metric", "value"], claims)
    write_csv(ctx.run_dir / "claims_extra.csv", ["claim_id", "scope", "subject", "metric", "value", "display", "formula"],
              extra)
    write_json(ctx.run_dir / "memo_checks.json", {"status": final.get("status"), "check": chk, "attempts": attempts,
                                                  "cache_hit": final.get("cache_hit", False),
                                                  "evidence_review_ids": sorted(review_ids),
                                                  "supplied_issue_ids": sorted(issue_ids)})
    (ctx.run_dir / "memo_input.txt").write_text(payload, encoding="utf-8")
    ctx.log("stage_end", stage="memo", status=final.get("status"), claims=len(claims), extra_claims=len(extra),
            seconds=round(time.perf_counter() - t0, 3))
    return final
