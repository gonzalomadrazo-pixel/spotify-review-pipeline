#!/usr/bin/env python3
"""Operator script: runs the long pipeline sequence and the local dashboard (standard library only).

The agent coordinator itself is pipeline/orchestrator.py (fixed stage order, saved handoffs). This script
only sequences whole runs and services so they survive a closed chat and a sleeping laptop:

    python3 orchestrate.py status                 # Ollama, keys, runs, dashboard ports
    python3 orchestrate.py runs                   # dev500 -> dev10k -> final100k (resumable; skips finished runs)
    python3 orchestrate.py runs --interrupt-after 20   # final run: STOP after 20 min, save, then resume
    python3 orchestrate.py runs --detach          # same, but survives closing the terminal or the app
    python3 orchestrate.py stop                   # graceful stop of a detached sequence (progress is saved)
    python3 orchestrate.py dashboard --run-id final100k --input data/subset_100k.csv
    python3 orchestrate.py logs final100k

Every run reuses saved results (exact-text cache), so re-running a finished or interrupted step costs no
new model work for completed reviews. Logs go to runs/_orchestrator/.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = str(ROOT / ".venv" / "bin" / "python")
LOGS = ROOT / "runs" / "_orchestrator"
FULL = "data/raw/spotify_reviews_18months.csv"
SEQUENCE = [
    ("dev500", "data/raw/checkpoint_500.csv", []),
    ("dev10k", "data/raw/analysis_10000.csv", []),
    ("final100k", "data/subset_100k.csv", ["--full-input", FULL]),
]


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def port_busy(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):  # Vite binds ::1 on macOS
        with socket.socket(family) as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                return True
    return False


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


def run_state(run_id: str) -> dict | None:
    p = ROOT / "runs" / run_id / "run_summary.json"
    if not p.exists():
        return None
    s = json.loads(p.read_text())
    last = (s.get("invocations") or [{}])[-1]
    return {"coverage": s["coverage"], "stop_reason": last.get("stop_reason"), "spend": s.get("spend_usd_actual")}


def finished(run_id: str) -> bool:
    st = run_state(run_id)
    return bool(st and st["coverage"]["pending"] == 0 and st["stop_reason"] == "completed"
                and (ROOT / "runs" / run_id / "memo.md").exists())


def keep_awake_cmd(cmd: list[str]) -> list[str]:
    # caffeinate holds an idle-sleep assertion only while the child runs; no system setting is changed.
    return ["caffeinate", "-i", *cmd] if sys.platform == "darwin" else cmd


def pipeline_run(run_id: str, inp: str, extra: list[str], log) -> subprocess.Popen:
    cmd = keep_awake_cmd([PY, "-m", "pipeline", "run", "--run-id", run_id, "--input", inp, *extra])
    say(f"start {run_id}: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    return proc


def cmd_runs(args) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    if args.detach:
        detach("runs.log")
    if not http_ok("http://127.0.0.1:11434/api/version"):
        say("Ollama is not reachable on 127.0.0.1:11434; open the Ollama app first.")
        return 1
    for run_id, inp, extra in SEQUENCE:
        if args.only and run_id != args.only:
            continue
        if finished(run_id):
            say(f"{run_id} already complete: {run_state(run_id)['coverage']}")
            continue
        with (LOGS / f"{run_id}.log").open("a", encoding="utf-8") as log:
            interrupt = args.interrupt_after if run_id == "final100k" else None
            attempt = 0
            while True:
                attempt += 1
                proc = pipeline_run(run_id, inp, extra, log)
                if interrupt and attempt == 1 and run_state(run_id) is None:
                    deadline = time.time() + 60 * interrupt
                    while proc.poll() is None and time.time() < deadline:
                        time.sleep(5)
                    if proc.poll() is None:
                        say(f"{run_id}: requesting a graceful stop (STOP file) to demonstrate interruption/resume")
                        (ROOT / "runs" / run_id / "STOP").touch()
                rc = proc.wait()
                st = run_state(run_id)
                say(f"{run_id} exited rc={rc} stop={st and st['stop_reason']} coverage={st and st['coverage']}")
                if rc != 0:
                    say(f"{run_id} failed; see {LOGS / (run_id + '.log')}. Re-run this command to resume.")
                    return rc
                if finished(run_id):
                    break
                if attempt == 1 and st and st["stop_reason"] == "stop_file":
                    say(f"{run_id}: resuming after the saved checkpoint")
                    continue
                say(f"{run_id} stopped early ({st and st['stop_reason']}); re-run this command to resume.")
                return 1
    say("sequence done")
    return 0


def detach(log_name: str, pid_name: str = "orchestrator.pid") -> None:
    """Re-run this command in a new session, detached from the terminal/app, so closing either won't stop it."""
    LOGS.mkdir(parents=True, exist_ok=True)
    if os.fork():
        how = ("python3 orchestrate.py stop" if pid_name == "orchestrator.pid"
               else f"kill $(cat {LOGS / pid_name})")
        say(f"running detached; follow with: tail -f {LOGS / log_name}   stop with: {how}")
        os._exit(0)
    os.setsid()
    out = os.open(LOGS / log_name, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    os.dup2(out, 1)
    os.dup2(out, 2)
    os.dup2(os.open(os.devnull, os.O_RDONLY), 0)
    (LOGS / pid_name).write_text(str(os.getpid()))


def cmd_stop(_args) -> int:
    """Graceful stop: every run that is mid-invocation finishes its in-flight call, saves, and exits."""
    n = 0
    for run_id, _, _ in SEQUENCE:
        d = ROOT / "runs" / run_id
        if d.exists() and not finished(run_id):
            (d / "STOP").touch()
            n += 1
    pid_file = LOGS / "orchestrator.pid"
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text()), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass
    say(f"requested a graceful stop ({n} STOP file(s)); progress is saved. Resume with: python3 orchestrate.py runs --detach")
    return 0


def step(title: str, cmd: list[str], cwd: Path = ROOT, check: bool = True) -> int:
    say(f"{title}: {' '.join(cmd)}")
    rc = subprocess.run(cmd, cwd=cwd).returncode
    if rc and check:
        raise SystemExit(f"{title} failed (exit {rc})")
    return rc


def cmd_finalize(args) -> int:
    """After final100k completes: evaluations, grading export + self-check, dashboard data (code only, $0)."""
    run_id, inp, raw = "final100k", "data/subset_100k.csv", ROOT / "data" / "raw"
    if args.detach:
        detach("finalize.log", "finalize.pid")
    while args.wait and not finished(run_id):
        say(f"{run_id} still running; checking again in 5 minutes (Ctrl-C to stop waiting; the run is unaffected)")
        time.sleep(300)
    if not finished(run_id):
        say(f"{run_id} is not complete yet; run `python3 orchestrate.py status`, or add --wait.")
        return 1
    ckpts = sorted((ROOT / "runs" / run_id / "checkpoints").glob("inv*.json"))
    before = next((p for p in ckpts if json.loads(p.read_text())["phase"] == "initial"), None)
    after = next((p for p in ckpts if json.loads(p.read_text())["phase"] == "resume"
                  and json.loads(p.read_text())["reason"] == "completed"), None)
    if not before or not after:
        say(f"missing interruption/resume checkpoints in runs/{run_id}/checkpoints: {[p.name for p in ckpts]}")
        return 1
    memo = json.loads((ROOT / "runs" / run_id / "memo_checks.json").read_text())
    if memo.get("status") != "checks_passed":
        # Re-running a completed run makes no model calls for cached work; the memo check is recomputed by code.
        step("re-check memo with the current checker", [PY, "-m", "pipeline", "run", "--run-id", run_id, "--input", inp,
                                                       "--full-input", FULL])
    step("planted-error test", [PY, "-m", "pipeline", "planted-errors", "--run-id", run_id, "--n", "12"])
    step("end-to-end trace", [PY, "-m", "pipeline", "trace", "--run-id", run_id, "--out", "evals/trace.md"])
    if (ROOT / "evals" / "golden_50_labeled.csv").exists():
        step("golden-set evaluation", [PY, "-m", "pipeline", "eval-golden", "--run-id", run_id])
    else:
        say("golden labels not found at evals/golden_50_labeled.csv; skipping golden evaluation")
    step("grading export", [PY, "-m", "pipeline", "export-grading", "--run-id", run_id, "--input", inp,
                            "--out", "grading", "--before", str(before), "--after", str(after)])
    ref, report = LOGS / "local-reference.json", LOGS / "self-check.json"
    step("checker reference", [PY, str(raw / "check_submission.py"), "reference", "--full", FULL, "--analysis", inp,
                               "--out", str(ref)])
    step("checker self-check", [PY, str(raw / "check_submission.py"), "check", "--reference", str(ref),
                                "--submission", "grading", "--out", str(report)], check=False)
    r = json.loads(report.read_text())
    r = r[0] if isinstance(r, list) else r
    say(f"self-check: status={r.get('status')} issues={r.get('issue_counts')} "
        f"coverage_point_candidate={r.get('working_coverage_point_candidate')}")
    step("cost report with the final run's measured row", [PY, "cost/calculator.py"])
    step("README results block", [PY, "-m", "pipeline", "results", "--run-id", run_id, "--readme", "README.md"])
    step("load dashboard database", ["uv", "run", "--group", "dashboard", "python", "dashboard/load_db.py",
                                     "--run-id", run_id, "--input", inp])
    for name in ("enriched.jsonl", "records.jsonl"):  # 40-70 MB raw: committed gzipped, readers fall back to .gz
        src = ROOT / "runs" / run_id / name
        with src.open("rb") as fi, open(src.with_name(name + ".gz"), "wb") as raw_out, \
                gzip.GzipFile(fileobj=raw_out, mode="wb", compresslevel=9, mtime=0) as fo:
            shutil.copyfileobj(fi, fo)
    say(f"compressed runs/{run_id}/enriched.jsonl and records.jsonl to .gz for the repository")
    if args.deploy:
        step("deploy dashboard (Vercel, free plan)", ["vercel", "deploy", "--prod", "--yes"], cwd=ROOT / "dashboard")
    say("finalize done")
    return 0


def cmd_dashboard(args) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    if args.run_id:
        say(f"loading {args.run_id} into the local dashboard database")
        subprocess.run(["uv", "run", "--group", "dashboard", "python", "dashboard/load_db.py", "--run-id", args.run_id,
                        "--input", args.input], cwd=ROOT, check=True)
    procs = []
    for name, port, cmd, cwd in (
        ("api", 8010, ["uv", "run", "--group", "dashboard", "uvicorn", "dashboard.api.index:app", "--port", "8010"], ROOT),
        ("web", 5173, ["npm", "run", "dev", "--", "--port", "5173", "--strictPort"], ROOT / "dashboard" / "frontend"),
    ):
        if port_busy(port):
            say(f"{name}: port {port} already in use; not starting a second copy")
            continue
        log = (LOGS / f"dashboard-{name}.log").open("a", encoding="utf-8")
        procs.append((name, subprocess.Popen(cmd, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)))
    for _ in range(60):
        if http_ok("http://127.0.0.1:8010/api/health") and port_busy(5173):
            break
        time.sleep(1)
    say("dashboard: http://localhost:5173  (API http://localhost:8010/api/docs). Ctrl-C to stop.")
    try:
        while procs and all(p.poll() is None for _, p in procs):
            time.sleep(1)
        for name, p in procs:
            if p.poll() is not None:
                say(f"{name} exited with code {p.returncode}; see {LOGS / f'dashboard-{name}.log'}")
    except KeyboardInterrupt:
        pass
    finally:
        for _, p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
        for _, p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


def cmd_status(_args) -> int:
    ollama = http_ok("http://127.0.0.1:11434/api/version")
    say(f"Ollama: {'up' if ollama else 'NOT reachable'}")
    keys = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") if os.environ.get(k)]
    env = ROOT / ".env"
    if env.exists():
        keys += [l.split("=")[0] for l in env.read_text().splitlines() if "=" in l and l.split("=", 1)[1].strip()]
    say(f"paid API keys available: {sorted(set(keys)) or 'none (all model calls are local, $0 API)'}")
    for run_id, _, _ in SEQUENCE:
        st = run_state(run_id)
        log = ROOT / "runs" / run_id / "run_log.jsonl"
        alive = subprocess.run(["pgrep", "-f", f"pipeline run --run-id {run_id} "], capture_output=True).returncode == 0
        if alive and log.exists():
            import sqlite3
            last = json.loads(log.read_text().splitlines()[-1])
            with sqlite3.connect(f"file:{ROOT / 'state' / 'state.sqlite'}?mode=ro", uri=True) as db:
                counts = dict(db.execute("SELECT status, COUNT(*) FROM records WHERE run_id=? GROUP BY status", (run_id,)))
            say(f"{run_id}: RUNNING invocation {last.get('invocation')} — live {counts} "
                f"(last event {last['kind']} at {last['at'][11:19]} UTC)")
        elif st:
            say(f"{run_id}: {'COMPLETE' if finished(run_id) else 'incomplete (re-run to resume)'} {st['coverage']} "
                f"spend=${st['spend']}")
        elif log.exists():
            last = json.loads(log.read_text().splitlines()[-1])
            say(f"{run_id}: running, first invocation in progress (last event {last['kind']} at {last['at'][11:19]} UTC)")
        else:
            say(f"{run_id}: not started")
    for name, port in (("dashboard api", 8010), ("dashboard web", 5173)):
        say(f"{name} :{port} {'running' if port_busy(port) else 'stopped'}")
    return 0


def cmd_logs(args) -> int:
    path = LOGS / f"{args.name}.log"
    if not path.exists():
        say(f"no log at {path}")
        return 1
    print("".join(path.read_text(encoding="utf-8").splitlines(keepends=True)[-args.lines:]))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    r = sub.add_parser("runs")
    r.add_argument("--only", choices=[s[0] for s in SEQUENCE])
    r.add_argument("--interrupt-after", type=float, default=None, help="minutes before a graceful STOP of final100k")
    r.add_argument("--detach", action="store_true", help="keep running after this terminal or the app closes")
    r.set_defaults(func=cmd_runs)
    sub.add_parser("stop", help="graceful stop of the detached sequence").set_defaults(func=cmd_stop)
    f = sub.add_parser("finalize", help="after final100k: evaluations, grading/ + self-check, dashboard data")
    f.add_argument("--deploy", action="store_true", help="also redeploy the dashboard to Vercel (free plan)")
    f.add_argument("--wait", action="store_true", help="wait for final100k to finish, then run")
    f.add_argument("--detach", action="store_true", help="wait in the background; survives closing the app")
    f.set_defaults(func=cmd_finalize)
    d = sub.add_parser("dashboard")
    d.add_argument("--run-id")
    d.add_argument("--input", default="data/subset_100k.csv")
    d.set_defaults(func=cmd_dashboard)
    lg = sub.add_parser("logs")
    lg.add_argument("name")
    lg.add_argument("--lines", type=int, default=40)
    lg.set_defaults(func=cmd_logs)
    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
