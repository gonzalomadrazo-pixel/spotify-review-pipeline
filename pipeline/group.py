"""Stage 4b - Group naming (model role: GROUPING agent; code: membership, evidence packs, validation).

Membership is deterministic (rank.build_membership). For every issue, code selects a bounded,
seeded evidence pack of member quotes with review IDs; the grouping agent names the issue, writes a
grounded one-sentence summary, rates coherence and lists misfit review IDs. Code rejects IDs that
were not in the pack. Results are cached by (config, issue, pack) so reruns make no new calls.
"""

from __future__ import annotations

import hashlib
import json
import re
import time

from .common import TAXONOMY, canonical_json, now_iso, render_prompt, role_fingerprint, sha256_text, write_json
from .llm import Dispatcher, Request, Work

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "coherence": {"type": "string", "enum": ["high", "medium", "low"]},
        "misfit_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "coherence", "misfit_ids"],
    "additionalProperties": False,
}
SUB_DEFS = {code: desc for t in TAXONOMY["topics"].values() for code, desc in t["subtopics"].items()}


def evidence_pack(members: list[dict], k: int, seed: str) -> list[dict]:
    """Deterministic pack: members ordered by sha256(seed:issue:review_id), distinct quotes, >= 20 chars preferred."""
    ranked = sorted(members, key=lambda m: hashlib.sha256(f"{seed}:{m['issue_id']}:{m['review_id']}".encode()).hexdigest())
    pack, seen = [], set()
    for pass_min in (20, 1):
        for m in ranked:
            q = m["evidence_quote"].strip()
            if len(q) < pass_min or q.lower() in seen:
                continue
            seen.add(q.lower())
            pack.append({"review_id": m["review_id"], "quote": q[:300]})
            if len(pack) >= k:
                return pack
    return pack


class GroupDispatcher(Dispatcher):
    def __init__(self, ctx, client, gcfg, prompt, fp):
        super().__init__(ctx, client, "group", None)
        self.gcfg, self.prompt, self.fp = gcfg, prompt, fp
        self.results = {}

    def build(self, work):
        issue_id, pack = work.payload
        user = json.dumps({"issue_id": issue_id, "definition": SUB_DEFS.get(issue_id, ""), "evidence_pack": pack},
                          ensure_ascii=False, indent=1)
        return Request(role="group", model=self.gcfg["model"], system=self.prompt, user=user,
                       max_tokens=self.gcfg["max_tokens"], schema=SCHEMA, temperature=self.gcfg.get("temperature"),
                       timeout_s=self.gcfg.get("request_timeout_s", 120))

    def on_success(self, work, attempt, request_id):
        issue_id, pack = work.payload
        try:
            data = json.loads(attempt.text)
        except json.JSONDecodeError:
            data = None
        pack_ids = {p["review_id"] for p in pack}
        problems = []
        if not isinstance(data, dict):
            problems.append("unparseable")
        else:
            if len(data.get("title", "").split()) > 12 or not data.get("title", "").strip():
                problems.append("title_length")
            if re.search(r"\d", data.get("summary", "")):
                problems.append("summary_contains_numbers")
            unknown = [i for i in data.get("misfit_ids", []) if i not in pack_ids]
            if unknown:
                data["rejected_unknown_ids"] = unknown
                data["misfit_ids"] = [i for i in data["misfit_ids"] if i in pack_ids]
        if problems and work.attempt_invalid < self.limits["max_invalid_retries"]:
            self.ctx.log("invalid_output", role="group", issue_id=issue_id, problems=problems)
            return [Work(key=work.key + "-r", payload=work.payload, review_ids=work.review_ids, attempt_invalid=1,
                         meta=work.meta)]
        if problems:
            value = {"title": issue_id, "summary": SUB_DEFS.get(issue_id, ""), "coherence": "unknown",
                     "misfit_ids": [], "naming_status": "failed:" + ",".join(problems), "request_id": request_id}
        else:
            value = {**data, "naming_status": "model", "request_id": request_id}
        self.results[issue_id] = value
        with self.store.tx() as db:
            db.execute("INSERT OR REPLACE INTO aux_cache VALUES (?,?,?,?,?,?)",
                       (work.meta["cache_key"], "group", json.dumps(value, ensure_ascii=False), request_id,
                        self.ctx.run_id, now_iso()))
        return []

    def on_give_up(self, work, reason):
        issue_id, _ = work.payload
        self.results[issue_id] = {"title": issue_id, "summary": SUB_DEFS.get(issue_id, ""), "coherence": "unknown",
                                  "misfit_ids": [], "naming_status": "failed:" + reason}


def run_group(ctx, client, completed: list[dict], ranking: list[dict]) -> list[dict]:
    g = ctx.cfg["group"]
    prompt = render_prompt(g["prompt_file"])
    fp = role_fingerprint({"model": g["model"], "temperature": g.get("temperature")}, prompt, SCHEMA)
    ctx.log("stage_start", stage="group", issues=len(ranking))
    t0 = time.perf_counter()
    members = {}
    for r in completed:
        if r["intent"] in ("complaint", "cancellation"):
            members.setdefault(r["subtopic"], []).append({**r, "issue_id": r["subtopic"]})
    disp = GroupDispatcher(ctx, client, g, prompt, fp)
    packs, work = {}, []
    for row in ranking:
        iid = row["issue_id"]
        pack = evidence_pack(members[iid], g["examples_per_issue"], g["seed"])
        packs[iid] = pack
        key = "group:" + sha256_text(canonical_json({"fp": fp, "issue": iid, "pack": pack, "v": g["prompt_version"]}))
        cached = ctx.store.one("SELECT value_json FROM aux_cache WHERE cache_key=?", (key,))
        if cached:
            disp.results[iid] = {**json.loads(cached[0]), "cache_hit": True}
        else:
            work.append(Work(key=f"g-{iid}", payload=(iid, pack), review_ids=[p["review_id"] for p in pack],
                             meta={"cache_key": key, "artifact": f"issue_pack:{iid}"}))
    stop = disp.run(work, ctx.workers) if work else "completed"
    issues = []
    for row in ranking:
        iid = row["issue_id"]
        res = disp.results.get(iid, {"title": iid, "summary": SUB_DEFS.get(iid, ""), "coherence": "unknown",
                                     "misfit_ids": [], "naming_status": "not_run:" + stop})
        issues.append({"issue_id": iid, "topic": iid.split(".")[0], "definition": SUB_DEFS.get(iid, ""),
                       "rank": row["rank"], "complaint_count": row["complaint_count"],
                       "severity_sum": row["severity_sum"], "mean_severity": row["mean_severity"],
                       "priority_score": row["priority_score"], **{k: v for k, v in res.items()},
                       "evidence_pack": packs[iid],
                       "member_review_ids_file": "membership.csv"})
    write_json(ctx.run_dir / "issues.json", {"grouping_rule": "issue_id = enrichment subtopic code; one issue per "
                                             "complaint/cancellation record (allow_multi_issue=false)",
                                             "prompt_fingerprint": fp, "issues": issues})
    ctx.log("stage_end", stage="group", issues=len(issues), calls=len(work), stop_reason=stop,
            seconds=round(time.perf_counter() - t0, 3))
    return issues
