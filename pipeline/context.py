"""Run context: identity, invocation/phase bookkeeping, logging, stop conditions and spend ledger."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from .common import ROOT, append_jsonl, load_rates, now_iso, write_json
from .llm import Ledger, StopFlag
from .store import Store


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except Exception:
        pass
    return None


class RunContext:
    def __init__(self, cfg: dict, run_id: str, state_dir: Path, runs_dir: Path, budget_usd: float | None = None,
                 workers: int | None = None, max_minutes: float | None = None, rate_multiplier: float = 1.0,
                 command: str = ""):
        self.cfg = cfg
        self.run_id = run_id
        self.store = Store(state_dir)
        self.state_dir = Path(state_dir)
        self.run_dir = Path(runs_dir) / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        lim = cfg["limits"]
        self.budget = float(budget_usd if budget_usd is not None else lim["budget_usd"])
        self.workers = int(workers if workers is not None else lim["max_workers"])
        mm = max_minutes if max_minutes is not None else lim.get("max_minutes", 0)
        self.deadline = time.time() + 60 * mm if mm else None
        self.rates = load_rates(ROOT / cfg.get("rates_file", "config/rates.csv"), rate_multiplier)
        self.ledger = Ledger(self.store, run_id, self.budget, self.rates)
        self.stop = StopFlag(self.run_dir / "STOP")
        self.consecutive_invalid_requests = 0
        self.seq = 0
        self.command = command or " ".join(sys.argv)
        self.invocation = None
        self.phase = None

    # -- invocations ---------------------------------------------------------
    def begin_invocation(self):
        """Each CLI invocation of a run is numbered. The first is phase 'initial'; any later
        invocation that continues the same run is phase 'resume'."""
        prev = self.store.scalar("SELECT MAX(n) FROM invocations WHERE run_id=?", (self.run_id,))
        self.invocation = (prev or 0) + 1
        self.phase = "initial" if self.invocation == 1 else "resume"
        completed = self.completed_count()
        with self.store.tx():
            self.store.db.execute(
                "INSERT INTO invocations(run_id, n, phase, command, started_at, completed_before) VALUES (?,?,?,?,?,?)",
                (self.run_id, self.invocation, self.phase, self.command, now_iso(), completed))
        self.seq = int(self.store.scalar("SELECT COUNT(*) FROM calls WHERE run_id=?", (self.run_id,)) or 0)
        self.log("invocation_start", phase=self.phase, completed_before=completed, budget_usd=self.budget,
                 workers=self.workers, git=git_commit())
        if (self.run_dir / "STOP").exists():
            (self.run_dir / "STOP").unlink()
        self.stop.install()

    def end_invocation(self, stop_reason: str):
        completed = self.completed_count()
        with self.store.tx():
            self.store.db.execute(
                "UPDATE invocations SET ended_at=?, stop_reason=?, completed_after=? WHERE run_id=? AND n=?",
                (now_iso(), stop_reason, completed, self.run_id, self.invocation))
        self.log("invocation_end", stop_reason=stop_reason, completed_after=completed,
                 spent_usd=round(self.ledger.spent(), 6))
        self.save_checkpoint(stop_reason)

    def completed_count(self) -> int:
        return int(self.store.scalar("SELECT COUNT(*) FROM records WHERE run_id=? AND status='completed'",
                                     (self.run_id,)) or 0)

    def save_checkpoint(self, label: str):
        ids = [r[0] for r in self.store.q(
            "SELECT review_id FROM records WHERE run_id=? AND status='completed' ORDER BY idx", (self.run_id,))]
        path = self.run_dir / "checkpoints" / f"inv{self.invocation:02d}_{label}.json"
        write_json(path, {"run_id": self.run_id, "invocation": self.invocation, "phase": self.phase,
                          "saved_at": now_iso(), "reason": label, "completed_count": len(ids),
                          "completed_ids": ids})
        return path

    # -- misc ----------------------------------------------------------------
    def next_request_id(self, role: str) -> str:
        self.seq += 1
        return f"{self.run_id}-{role}-i{self.invocation}-{self.seq:06d}"

    def stop_requested(self):
        return self.stop.check()

    def log(self, kind: str, **data):
        row = {"at": now_iso(), "run_id": self.run_id, "invocation": self.invocation, "kind": kind, **data}
        append_jsonl(self.run_dir / "run_log.jsonl", row)
        self.store.event(self.run_id, self.invocation, row["at"], kind, data)
        if kind in ("stage_start", "stage_end", "budget_stop", "invocation_start", "invocation_end", "progress",
                    "call_failed", "retry_scheduled", "stage_skipped"):
            brief = {k: v for k, v in data.items() if k not in ("git",)}
            print(f"[{row['at'][11:19]}] {kind} {json.dumps(brief, ensure_ascii=False)[:300]}", flush=True)
