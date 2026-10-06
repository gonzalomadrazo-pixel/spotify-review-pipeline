"""Deterministic offline stand-in for the model, used only by tests and `--fake-model` dry runs.

It is a crude keyword labeler. Its outputs are NOT business evidence; runs made with it must use a
separate --state-dir/--runs-dir and are never exported to grading/ or cost/.
"""

from __future__ import annotations

import json
import re

from pipeline.llm import Attempt, ScriptedClient

RULES = [
    (r"hack|stole|unauthori[sz]ed|charged twice|double charg|refund", "billing.charges", 5),
    (r"log ?in|sign ?in|password", "access.login", 4),
    (r"crash|won'?t open|not open", "playback.crash", 4),
    (r"premium|shuffle|skip|choose|pick|repeat", "billing.free_tier_limits", 3),
    (r"\bads?\b|advert", "usability.ads", 2),
    (r"download|offline", "downloads.failure", 3),
    (r"lyric", "catalog.lyrics", 3),
    (r"stop|pause|lag|slow|freez", "playback.interruptions", 3),
]
NEG = re.compile(r"worst|bad|hate|terrible|poor|useless|trash|annoy|can'?t|cannot|not|uninstall|delete", re.I)
POS = re.compile(r"good|great|love|best|excellent|awesome|nice|amazing|super", re.I)
CANCEL = re.compile(r"uninstall|deleting|delete this|cancel|switch(ing)? to|moving to|leav(e|ing)", re.I)


def label(text: str) -> dict:
    t = text.lower()
    neg, pos = bool(NEG.search(t)), bool(POS.search(t))
    sub, sev = "other.general", 2
    for pat, s, v in RULES:
        if re.search(pat, t):
            sub, sev = s, v
            break
    if CANCEL.search(t):
        intent = "cancellation"
    elif neg:
        intent = "complaint"
    elif pos:
        intent, sev = "praise", 1
        sub = "other.general" if sub not in ("catalog.lyrics",) else sub
    elif "please" in t or "add " in t:
        intent, sev = "request", 1
    else:
        intent, sev, sub = "unclear", 1, "other.unclear"
    sent = -2 if intent in ("cancellation",) else -1 if intent == "complaint" else 1 if intent == "praise" else 0
    return {"sub": sub, "intent": intent, "sev": sev if intent in ("complaint", "cancellation") else 1, "sent": sent}


def _reviews(user: str):
    body = user.split("<reviews>\n", 1)[1].rsplit("\n</reviews>", 1)[0]
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def respond(req, n) -> Attempt:
    usage = {"input_tokens": len(req.user) // 4 + 50, "cache_creation_input_tokens": 0,
             "cache_read_input_tokens": len(req.system) // 4, "output_tokens": 0}
    if req.role == "enrich":
        items = []
        for r in _reviews(req.user):
            lab = label(r["text"])
            q = r["text"].split(".")[0][:120] if r["quote"] else ""
            items.append({"k": r["k"], "q": q, **lab, "review": False, "why": "none"})
        text = json.dumps({"items": items})
    elif req.role == "verify":
        items = []
        for r in _reviews(req.user):
            lab = label(r["text"])
            items.append({"k": r["k"], "topic": lab["sub"].split(".")[0], "intent": lab["intent"], "sev": lab["sev"],
                          "confident": True})
        text = json.dumps({"items": items})
    elif req.role == "group":
        p = json.loads(req.user)
        text = json.dumps({"title": p["issue_id"].replace(".", " ").replace("_", " ").title(),
                           "summary": "Customers describe problems matching this issue definition.",
                           "coherence": "high", "misfit_ids": []})
    else:  # memo
        facts = re.findall(r"\[(F\d+)\] issue (\S+) complaint_count = ([\d,]+)", req.user)
        fid, iid, val = facts[0] if facts else ("F01", "other.general", "0")
        text = (f"## Recommendation\nPrioritize `{iid}`.\n\n## Why\nIt has {val} complaints [{fid}].\n\n"
                "## Alternatives considered\nOther areas rank lower in the baseline.\n\n"
                "## Risks and limitations\nFake-model output for offline tests only.")
    usage["output_tokens"] = len(text) // 4
    return Attempt(ok=True, text=text, stop_reason="end_turn", usage=usage, message_id=f"fake_msg_{n}",
                   provider_request_id=f"fake_req_{n}")


def fake_client(faults=None):
    """faults: optional callable (req, n) -> Attempt | None to inject failures before normal responses."""
    def responder(req, n):
        if faults:
            a = faults(req, n)
            if a is not None:
                return a
        return respond(req, n)
    return ScriptedClient(responder)


class FakeBatchClient(ScriptedClient):
    """Adds an in-memory Message Batches API: jobs end after `polls_until_end` retrieve calls."""

    def __init__(self, responder=None, polls_until_end=1):
        super().__init__(responder or respond)
        self.jobs, self.polls, self.polls_until_end = {}, {}, polls_until_end

    def submit_batch(self, requests):
        bid = f"msgbatch_fake_{len(self.jobs) + 1}"
        self.jobs[bid] = requests
        self.polls[bid] = 0
        return bid

    def retrieve_batch(self, batch_id):
        self.polls[batch_id] += 1
        done = self.polls[batch_id] >= self.polls_until_end
        return ("ended" if done else "in_progress"), {"processing": 0 if done else len(self.jobs[batch_id])}

    def batch_results(self, batch_id):
        for cid, req in self.jobs[batch_id]:
            yield cid, self.call(req)
