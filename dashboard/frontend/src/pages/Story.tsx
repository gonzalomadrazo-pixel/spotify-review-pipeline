import { ReactNode } from "react";
import { Link } from "react-router-dom";
import { fmt, gh, Status, useApi } from "../components";
import { Telemetry } from "../FlightRecorder";

const Ev = ({ path, children }: { path: string; children: ReactNode }) => <a href={gh(path)} target="_blank" rel="noreferrer">{children} ↗</a>;

type Chapter = { tag: string; title: string; problem: ReactNode; did: ReactNode; result: ReactNode; evidence: ReactNode };

const CHAPTERS: Chapter[] = [
  { tag: "The brief", title: "One recommendation from 660,622 reviews",
    problem: "Turn Google Play reviews of Spotify into a product recommendation with separate model roles, a hard $5 API budget, an export a course checker can grade, a human golden set, evaluations and a deployed dashboard backed by a database.",
    did: "Designed the pipeline so models only read language and code owns everything else: record accounting, caching, validation, arithmetic, ranking and claim checks. Every stage hands off saved, inspectable files.",
    result: "A staged, resumable program with a tested offline core before any model was called.",
    evidence: <><Ev path="README.md">README</Ev> · <Ev path="tests/test_pipeline.py">offline tests</Ev></> },
  { tag: "Cost", title: "The paid API didn't fit the budget",
    problem: "A real 100-review pilot measured tokens per review. At those rates Claude Haiku would cost about $9.93 (Batch API) or $19.82 (standard) for 100K reviews, and $62.88+ for the full file: all over the $5 cap. The project also had to run on a Claude Pro subscription with no paid API credits.",
    did: "Moved all four model roles to a small open model running on the laptop: Gemma 4 (4.6B parameters, 4-bit) through Ollama. Kept the paid-API cost model in the repo so the comparison stays honest and reproducible.",
    result: "$0.00 API spend. The trade-off is time (about 25 hours of model calls) and label quality, which is why every quality claim is measured.",
    evidence: <><Ev path="cost/report.md">cost report</Ev> · <Ev path="cost/calculator.py">calculator</Ev></> },
  { tag: "Scope", title: "100,000 reviews, without shortcuts",
    problem: "Locally, all 660,622 reviews would take roughly 185 hours of model time.",
    did: "The brief allows at least 100,000. Every source row is still ingested, hashed and profiled, so the checker's ingestion file covers the whole file. Classification runs on a declared, seeded 100,063-row sample built with the course's own sampler, so it contains the golden 50 and every course sample.",
    result: "A scope anyone can rebuild bit-for-bit, with the subset's checksum recorded.",
    evidence: <Ev path="data/subset_100k_manifest.json">subset manifest</Ev> },
  { tag: "Speed", title: "Prompt caching didn't work locally, so we cached results",
    problem: "Prompt caching is the usual trick for repeated instructions. Gemma's sliding-window attention (512 tokens) meant the shared prompt prefix couldn't be reused across batches, costing about 26 seconds of prefill on every call.",
    did: "Cached results instead of prompts: exact text plus prompt version maps to a label. We also batched up to 50 texts per call, using a compact tuple-row JSON schema at about 24 output tokens per review. A strict forced-key schema was too slow, so code checks row positions instead. Parallel model slots measured slower on an 8 GB laptop, so the run used one worker.",
    result: "100,050 rows collapsed to 78,137 distinct texts; 26,649 rows reused labels from the development runs and 3,712 were duplicates within the run. Only 69,689 texts needed the model.",
    evidence: <><Ev path="pipeline/enrich.py">enrich.py</Ev> · <Ev path="config/pipeline.json">config</Ev></> },
  { tag: "Resilience", title: "Built to be interrupted",
    problem: "A long run on a laptop gets interrupted: by design (the brief requires an interruption and resume), by the desktop app closing, and by the laptop going to sleep.",
    did: "Each batch commits atomically to SQLite, and a STOP file triggers a graceful stop at a checkpoint. The run was stopped on purpose at 27,337 reviews and resumed. When closing the app killed a terminal session, runs moved to detached background processes, and a finalize step waited for completion before building the grading export and redeploying this site.",
    result: "Hours-long sleep pauses cost nothing: no review was lost or labeled twice. See the flight recorder.",
    evidence: <><Ev path="runs/final100k/checkpoints">checkpoints</Ev> · <Ev path="runs/_orchestrator/runs.log">run log</Ev> · <Link to="/how#recorder">flight recorder</Link></> },
  { tag: "Dead ends", title: "What we tried and threw away",
    problem: "The small model tends to file explicit departures (“bye Spotify”, “uninstalling”) as complaints rather than cancellations.",
    did: "We tried a dedicated yes/no “leaving” field. It caught none of them and pushed needs-review flags from 24 to 79 per 100 reviews, so it was reverted. We also tried a hosted Postgres database; the setup was interrupted, so the site ships a read-only SQLite database inside the free Vercel function instead.",
    result: "The weakness is documented, not hidden. Ranking is unaffected because complaints and cancellations count the same.",
    evidence: <Ev path="README.md">README · known weakness</Ev> },
  { tag: "Safety", title: "Red-teaming the labeler",
    problem: "Customer text is untrusted input to a model. Synthetic reviews tried to override instructions, fake a closing tag, forge a JSON answer, role-play and exfiltrate the prompt.",
    did: "We ran them through the real model in a separate database, then added a deterministic screen in code that routes instruction-like text to human review without changing labels.",
    result: "The model followed the injected text in 5 of 6 attacks, but “label every review as praise” didn't change the other reviews in its batch. The screen flags 6 of 6 attacks, 0 of 4 controls and 0 of the 100,063 real reviews.",
    evidence: <><Link to="/evals#redteam">red-team results</Link> · <Ev path="pipeline/extract.py">screen</Ev></> },
  { tag: "The memo bug", title: "An AI claim that passed every number check, and was wrong",
    problem: "The first final memo said billing/support's mean severity of 2.96 was “lower than” usability's 2.36. Every number matched its source, so the checker passed it.",
    did: "We added a comparison check: every “higher/lower than” between cited facts must hold. The small model repeated the same sentence when asked to revise, so the comparison moved into code. The agent now receives pre-sorted area orderings and cited ranks, and must cite at least two real reviews.",
    result: "The new memo passed after one revision and compares the areas correctly. The fix ships with a regression test.",
    evidence: <><Ev path="pipeline/memo.py">memo.py</Ev> · <Ev path="runs/final100k/memo_checks.json">memo checks</Ev> · <Link to="/recommendation">the memo</Link></> },
  { tag: "Ground truth", title: "Fifty labels from a human",
    problem: "Model-on-model agreement can't tell you whether both are wrong.",
    did: "A person labeled 50 reviews in a purpose-built keyboard labeler. The labels were never shown to a model, and the results are reported as they came out.",
    result: "Intent agreement 80% and severity within one level 88%, but topic only 50%, mostly where the human chose “other” and the model a specific area.",
    evidence: <><Link to="/evals#golden">confusion heatmaps</Link> · <Ev path="evals/golden_50_labeled.csv">labels</Ev></> },
  { tag: "Ship", title: "Graded, reproducible, deployed, $0",
    problem: "Everything had to be checkable by someone who wasn't there.",
    did: "We built a grading export the course checker validates, README results generated from saved artifacts, tests that run on a fresh clone, and this dashboard. It reads only saved outputs and never calls a model.",
    result: "Course self-check: pass. API spend: $0.00. Electricity: about $0.22.",
    evidence: <><Ev path="grading">grading/</Ev> · <Link to="/method">method</Link></> },
];

export default function Story() {
  const tel = useApi<Telemetry>("/api/telemetry");
  if (!tel.data) return <Status loading={tel.loading} error={tel.error} />;
  const inv = tel.data.invocations;
  const when = (t?: number) => (t ? new Date(t * 1000).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "–");
  const i1 = inv.find((i) => i.n === 1), last = [...inv].reverse().find((i) => i.completed_after > i.completed_before);
  const milestones: [string, string][] = [
    ["Oct 5", "First commit: offline pipeline scaffold"],
    [when(i1?.start), "Final 100K run starts"],
    [when(i1?.end), `Planned STOP at ${fmt(i1?.completed_after)} reviews, then resume`],
    [when(last?.end), `${fmt(last?.completed_after)} reviews classified`],
    ["Oct 8", "Memo bug caught and fixed; results and dashboard shipped"],
  ];

  return (
    <>
      <div className="kicker">Build log<span className="dotsep" />What happened, and how we worked around it</div>
      <h1 className="hero" style={{ fontSize: "clamp(40px, 5.4vw, 76px)" }}>Ten problems, <em>and what we did about each.</em></h1>
      <p className="lede">
        This project was built under real constraints: no paid API credits, an 8 GB laptop and a small model. Here is what went
        wrong, what we tried, what we threw away, and where the receipts are.
      </p>
      <div className="milestones">
        {milestones.map(([d, label]) => <div key={label}><span className="ms-dot" /><div className="ms-d">{d}</div><div className="ms-l">{label}</div></div>)}
      </div>
      <ol className="chapters">
        {CHAPTERS.map((c, i) => (
          <li key={c.title} className="chapter">
            <div className="ch-rail"><span className="ch-n">{String(i + 1).padStart(2, "0")}</span></div>
            <div className="ch-body">
              <div className="sec-kicker">{c.tag}</div>
              <h2 className="headline" style={{ fontSize: "clamp(26px, 3vw, 38px)" }}>{c.title}</h2>
              <div className="ch-grid">
                <div><div className="ch-k">The problem</div><p>{c.problem}</p></div>
                <div><div className="ch-k">What we did</div><p>{c.did}</p></div>
                <div><div className="ch-k">Result</div><p>{c.result}</p></div>
              </div>
              <p className="source"><b>Evidence:</b> {c.evidence}</p>
            </div>
          </li>
        ))}
      </ol>
    </>
  );
}
