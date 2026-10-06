"""Model access, spend ledger and the bounded dispatch loop shared by every model role.

Design:
- A worker thread performs exactly ONE HTTP attempt (SDK auto-retries are disabled) so every
  attempt, including failures, gets its own row in the call log.
- The main thread owns the SQLite store, the spend ledger and retry scheduling.
- Before dispatch, the ledger reserves a worst-case cost (uncached input estimate + max_tokens
  output). Work is admitted only while spent + reserved + next reservation <= budget.
- Transient errors (429/5xx/overloaded/timeouts/connection) are retried with exponential backoff
  and jitter, up to max_transient_retries. A provider spend-cap error stops the run immediately.
"""

from __future__ import annotations

import json
import os
import random
import signal
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from .common import now_iso, usage_cost

# ---------------------------------------------------------------- results


@dataclass
class Attempt:
    ok: bool
    text: str = ""
    stop_reason: str | None = None
    usage: dict | None = None
    message_id: str | None = None
    provider_request_id: str | None = None
    error: str | None = None
    error_kind: str | None = None  # transient | fatal | budget | invalid_request
    retry_after: float | None = None
    unknown_charge: bool = False
    started_at: str = ""
    ended_at: str = ""
    duration_s: float = 0.0


@dataclass
class Request:
    role: str
    model: str
    system: str
    user: str
    max_tokens: int
    schema: dict | None = None
    temperature: float | None = None
    effort: str | None = None
    timeout_s: float = 120.0


class StopRun(Exception):
    pass


# ---------------------------------------------------------------- clients


class AnthropicClient:
    """Thin wrapper over the official SDK; one attempt per call, no SDK retries."""

    def __init__(self):
        import anthropic
        from dotenv import load_dotenv

        load_dotenv()
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key "
                             "(paid commands only; offline commands never need it).")
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=0)

    def build_params(self, req: Request) -> dict:
        params = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            # Static instructions first and marked cacheable; reviews go in the user turn.
            "system": [{"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": req.user}],
        }
        output_config = {}
        if req.schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": req.schema}
        if req.effort:
            output_config["effort"] = req.effort
        if output_config:
            params["output_config"] = output_config
        if req.temperature is not None:
            params["temperature"] = req.temperature
        return params

    def call(self, req: Request) -> Attempt:
        a = self.anthropic
        params = self.build_params(req)
        extra_body = {}
        if "temperature" in params:
            extra_body["temperature"] = params.pop("temperature")
        started, t0 = now_iso(), time.perf_counter()
        try:
            raw = self.client.with_options(timeout=req.timeout_s).messages.with_raw_response.create(
                **params, extra_body=extra_body or None)
            msg = raw.parse()
            text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
            u = msg.usage
            usage = {
                "input_tokens": u.input_tokens or 0,
                "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
                "cache_read_input_tokens": u.cache_read_input_tokens or 0,
                "output_tokens": u.output_tokens or 0,
            }
            return Attempt(ok=True, text=text, stop_reason=msg.stop_reason, usage=usage, message_id=msg.id,
                           provider_request_id=raw.headers.get("request-id"), started_at=started,
                           ended_at=now_iso(), duration_s=time.perf_counter() - t0)
        except a.RateLimitError as e:
            body = getattr(e, "body", None) or {}
            details = (body.get("error") or {}).get("details") or {} if isinstance(body, dict) else {}
            kind = "budget" if details.get("error_code") == "enforced_spend_limit_reached" else "transient"
            ra = e.response.headers.get("retry-after") if e.response is not None else None
            return self._err(started, t0, f"429 {e.message}", kind, retry_after=float(ra) if ra else None, exc=e)
        except a.BadRequestError as e:
            kind = "budget" if "usage limits" in str(e.message) else "invalid_request"
            return self._err(started, t0, f"400 {e.message}", kind, exc=e)
        except (a.AuthenticationError, a.PermissionDeniedError, a.NotFoundError) as e:
            return self._err(started, t0, f"{e.status_code} {e.message}", "fatal", exc=e)
        except a.APITimeoutError as e:
            # The provider may still have processed (and billed) a timed-out request.
            return self._err(started, t0, f"timeout {e}", "transient", unknown_charge=True)
        except a.APIConnectionError as e:
            return self._err(started, t0, f"connection {e}", "transient")
        except a.APIStatusError as e:
            kind = "transient" if e.status_code >= 500 or e.status_code in (408, 409, 529) else "fatal"
            return self._err(started, t0, f"{e.status_code} {e.message}", kind, exc=e)

    # -- Message Batches API (enrichment transport option) ----------------
    def submit_batch(self, requests: list[tuple[str, Request]]) -> str:
        reqs = []
        for cid, req in requests:
            params = self.build_params(req)
            reqs.append({"custom_id": cid, "params": params})
        batch = self.client.messages.batches.create(requests=reqs)
        return batch.id

    def retrieve_batch(self, batch_id: str):
        b = self.client.messages.batches.retrieve(batch_id)
        c = b.request_counts
        return b.processing_status, {"processing": c.processing, "succeeded": c.succeeded, "errored": c.errored,
                                     "canceled": c.canceled, "expired": c.expired}

    def batch_results(self, batch_id: str):
        from .batch_api import attempt_from_batch_result
        for result in self.client.messages.batches.results(batch_id):
            yield attempt_from_batch_result(result)

    @staticmethod
    def _err(started, t0, msg, kind, retry_after=None, unknown_charge=False, exc=None):
        rid = None
        if exc is not None and getattr(exc, "response", None) is not None:
            rid = exc.response.headers.get("request-id")
        return Attempt(ok=False, error=msg[:500], error_kind=kind, retry_after=retry_after,
                       unknown_charge=unknown_charge, provider_request_id=rid, started_at=started,
                       ended_at=now_iso(), duration_s=time.perf_counter() - t0)


class ScriptedClient:
    """Offline test double. `responder(req, n)` returns an Attempt; used for failure-injection tests."""

    def __init__(self, responder):
        self.responder = responder
        self.n = 0
        self.lock = threading.Lock()

    def call(self, req: Request) -> Attempt:
        with self.lock:
            self.n += 1
            n = self.n
        started = now_iso()
        a = self.responder(req, n)
        a.started_at, a.ended_at = started, now_iso()
        return a


# ---------------------------------------------------------------- ledger


class Ledger:
    def __init__(self, store, run_id: str, budget_usd: float, rates: dict):
        self.store, self.run_id, self.budget, self.rates = store, run_id, float(budget_usd), rates
        self.reserved = 0.0

    def spent(self) -> float:
        return self.store.run_spend(self.run_id)

    def external_reserved(self) -> float:
        # Submitted-but-unfinished Message Batches hold a reservation until their results arrive.
        return float(self.store.scalar(
            "SELECT COALESCE(SUM(reserved_usd),0) FROM batch_jobs WHERE run_id=? AND status NOT IN ('processed','canceled')",
            (self.run_id,)) or 0.0)

    def can_reserve(self, amount: float) -> bool:
        return self.spent() + self.reserved + self.external_reserved() + amount <= self.budget + 1e-12

    def estimate(self, model: str, tier: str, input_tokens_est: int, max_tokens: int) -> float:
        cost, _ = usage_cost(self.rates, model, tier, {"input_tokens": input_tokens_est, "output_tokens": max_tokens})
        return cost


def estimate_input_tokens(system: str, user: str) -> int:
    # Conservative: ~3 characters per token plus fixed overhead (schema, message framing).
    return int((len(system) + len(user)) / 3.0) + 600


# ---------------------------------------------------------------- dispatch loop


@dataclass
class Work:
    """One unit of model work (e.g. one enrichment batch). `payload` is role-specific."""
    key: str
    payload: object
    review_ids: list
    attempt_invalid: int = 0
    attempt_transient: int = 0
    not_before: float = 0.0
    tier: str = "standard"
    meta: dict = field(default_factory=dict)


class Dispatcher:
    """Runs Work items through a model with bounded concurrency, retries, spend and time caps."""

    def __init__(self, ctx, client, role: str, label_config: str | None):
        self.ctx, self.client, self.role, self.label_config = ctx, client, role, label_config
        self.store = ctx.store
        self.limits = ctx.cfg["limits"]
        self.stop_reason = None
        self._last_dispatch = 0.0

    # Hooks supplied by the role module ------------------------------------
    def build(self, work: Work) -> Request:  # pragma: no cover - overridden
        raise NotImplementedError

    def on_success(self, work: Work, attempt: Attempt, request_id: str) -> list[Work]:  # pragma: no cover
        """Validate + commit. Return follow-up Work (e.g. retry of invalid items)."""
        raise NotImplementedError

    def on_give_up(self, work: Work, reason: str) -> None:  # pragma: no cover
        raise NotImplementedError

    # Loop ---------------------------------------------------------------------
    def run(self, items: list[Work], workers: int) -> str:
        ctx = self.ctx
        queue = list(items)
        inflight = {}
        rpm = max(1, int(self.limits.get("requests_per_minute", 60)))
        min_gap = 60.0 / rpm
        deadline = ctx.deadline
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            while queue or inflight:
                if ctx.stop_requested() and not self.stop_reason:
                    self.stop_reason = ctx.stop_requested()
                if deadline and time.time() > deadline and not self.stop_reason:
                    self.stop_reason = "time_cap"
                # Admit new work.
                while not self.stop_reason and queue and len(inflight) < workers:
                    now = time.time()
                    ready = [w for w in queue if w.not_before <= now]
                    if not ready:
                        break
                    work = ready[0]
                    req = self.build(work)
                    est = ctx.ledger.estimate(req.model, work.tier, estimate_input_tokens(req.system, req.user), req.max_tokens)
                    if not ctx.ledger.can_reserve(est):
                        self.stop_reason = "budget_cap"
                        ctx.log("budget_stop", role=self.role, spent=round(ctx.ledger.spent(), 6),
                                reserved=round(ctx.ledger.reserved, 6), next_reservation=round(est, 6),
                                budget=ctx.ledger.budget)
                        break
                    gap = time.time() - self._last_dispatch
                    if gap < min_gap:
                        time.sleep(min_gap - gap)
                    self._last_dispatch = time.time()
                    queue.remove(work)
                    request_id = ctx.next_request_id(self.role)
                    ctx.ledger.reserved += est
                    self.store.insert_call({
                        "request_id": request_id, "run_id": ctx.run_id, "invocation": ctx.invocation,
                        "role": self.role, "phase": ctx.phase, "model": req.model,
                        "label_config": work.meta.get("label_config", self.label_config), "tier": work.tier,
                        "review_ids": json.dumps(work.review_ids), "n_items": len(work.review_ids),
                        "attempt": work.attempt_invalid + work.attempt_transient + 1, "outcome": "dispatched",
                        "reserved_usd": est, "started_at": now_iso(), "artifact": work.meta.get("artifact"),
                        "seq": ctx.seq,
                    })
                    fut = pool.submit(self.client.call, req)
                    inflight[fut] = (work, req, request_id, est)
                if not inflight:
                    if self.stop_reason or not queue:
                        break
                    time.sleep(min(0.5, max(0.0, min(w.not_before for w in queue) - time.time())))
                    continue
                done, _ = wait(list(inflight), timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in done:
                    work, req, request_id, est = inflight.pop(fut)
                    ctx.ledger.reserved -= est
                    attempt = fut.result()
                    follow = self._finish(work, req, request_id, attempt)
                    queue.extend(follow)
                if self.stop_reason and not inflight:
                    break
        if self.stop_reason:
            for w in queue:
                ctx.log("work_not_admitted", role=self.role, key=w.key, n=len(w.review_ids), reason=self.stop_reason)
        return self.stop_reason or "completed"

    def _finish(self, work: Work, req: Request, request_id: str, attempt: Attempt) -> list[Work]:
        ctx = self.ctx
        usage = attempt.usage
        if usage is not None:
            cost, _ = usage_cost(ctx.ledger.rates, req.model, work.tier, usage)
            basis = "actual_usage"
        elif attempt.unknown_charge:
            cost, basis = 0.0, "unknown_timeout"  # flagged for reconciliation with the provider dashboard
        else:
            cost, basis = 0.0, "no_usage_error"
        fields = dict(
            ended_at=attempt.ended_at, duration_s=round(attempt.duration_s, 3), stop_reason=attempt.stop_reason,
            message_id=attempt.message_id, provider_request_id=attempt.provider_request_id,
            input_tokens=(usage or {}).get("input_tokens", 0), output_tokens=(usage or {}).get("output_tokens", 0),
            cache_creation_input_tokens=(usage or {}).get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=(usage or {}).get("cache_read_input_tokens", 0),
            usage_available=int(usage is not None), cost_usd=cost, cost_basis=basis,
            unknown_charge=int(attempt.unknown_charge))
        if not attempt.ok:
            with self.store.tx():
                self.store.update_call(request_id, outcome="failed", error=attempt.error, **fields)
            ctx.log("call_failed", role=self.role, request_id=request_id, error=attempt.error, error_kind=attempt.error_kind)
            if attempt.error_kind == "budget":
                self.stop_reason = "provider_spend_limit"
                return [work]
            if attempt.error_kind in ("fatal",):
                self.stop_reason = "fatal_api_error"
                return [work]
            if attempt.error_kind == "invalid_request":
                ctx.consecutive_invalid_requests += 1
                if ctx.consecutive_invalid_requests >= 3:
                    self.stop_reason = "repeated_invalid_requests"
                    return [work]
                self.on_give_up(work, "invalid_request")
                return []
            if work.attempt_transient < self.limits["max_transient_retries"]:
                work.attempt_transient += 1
                base = self.limits["backoff_base_s"] * (2 ** (work.attempt_transient - 1))
                delay = min(self.limits["backoff_max_s"], max(base, attempt.retry_after or 0)) * (0.5 + random.random())
                work.not_before = time.time() + delay
                ctx.log("retry_scheduled", role=self.role, key=work.key, delay_s=round(delay, 2),
                        transient_attempt=work.attempt_transient)
                return [work]
            self.on_give_up(work, "api_error_after_retries")
            return []
        ctx.consecutive_invalid_requests = 0
        with self.store.tx():
            self.store.update_call(request_id, outcome="succeeded", **fields)
        return self.on_success(work, attempt, request_id)


# ---------------------------------------------------------------- interruption


class StopFlag:
    """SIGINT/SIGTERM or a STOP file in the run directory request a graceful stop: in-flight calls
    finish and are saved; no new work is admitted."""

    def __init__(self, stop_file):
        self.reason = None
        self.stop_file = stop_file
        self._installed = False

    def install(self):
        if self._installed:
            return

        def handler(signum, frame):
            if self.reason:  # second Ctrl-C: hard exit (atomic commits keep state consistent)
                raise KeyboardInterrupt
            self.reason = "interrupted_by_signal"
            print("\n[stop] interrupt received: finishing in-flight calls, saving progress...", flush=True)

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        self._installed = True

    def check(self):
        if not self.reason and self.stop_file.exists():
            self.reason = "stop_file"
        return self.reason
