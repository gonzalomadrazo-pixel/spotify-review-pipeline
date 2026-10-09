import { useState } from "react";
import { Link } from "react-router-dom";
import { Review } from "../api";
import { Card, Contents, fmt, gh, Section, Sev, Status, Tag, useApi } from "../components";
import FlightRecorder, { Telemetry } from "../FlightRecorder";
import { ACCENT, areaColor, NEUTRAL } from "../theme";

type Overview = { total: number; issue_count: number; memo: { status: string; model: string } | null;
  run: { run_id: string; budget_usd: number; spend_usd_actual: number; scope: { analysis_rows?: number; full_rows?: number } };
  status_counts: Record<string, number> };
type Scenario = { model: string; tier: string; usd: { total_api: number }; local_hours?: number; exceeds_budget: boolean };
type Evidence = {
  verify_summary?: { verified: number; topic_agree_rate: number; intent_agree_rate: number };
  cost_replay?: { projections: Record<string, { volume: { rows: number }; scenarios: Record<string, Scenario> }> };
};
type ReviewTrace = Review & {
  issue: { issue_id: string; rank: number; title: string; priority_score: number } | null;
  verification: { verify_topic: string; verify_intent: string; verify_severity: number; disagreement: number } | null;
};

// A review the final memo cites as evidence, so the trace ends at a sentence of the recommendation.
const TRACE_ID = "044b0128-b99d-44d3-8dab-bfe34cb21dee";
const TOC: [string, string][] = [
  ["pipeline", "The pipeline"], ["funnel", "Compute funnel"], ["recorder", "Flight recorder"],
  ["trace", "One review, end to end"], ["economics", "Inference economics"],
];

type Stage = { k: string; who: "data" | "code" | "model"; title: string; stat: string; what: string; art: [string, string][] };

export default function How() {
  const ov = useApi<Overview>("/api/overview");
  const tel = useApi<Telemetry>("/api/telemetry");
  const ev = useApi<Evidence>("/api/evidence");
  const tr = useApi<ReviewTrace>(`/api/reviews/${TRACE_ID}`);
  const rec = useApi<{ memo: { markdown: string } }>("/api/recommendation");
  const [open, setOpen] = useState("enrich");
  if (!ov.data || !tel.data) return <Status loading={ov.loading || tel.loading} error={ov.error || tel.error} />;
  const o = ov.data, t = tel.data;
  const byRole = (r: number) => t.calls.filter((c) => c[3] === r);
  const enrich = byRole(0);
  const okEnrich = enrich.filter((c) => c[5]);
  const textsSent = okEnrich.reduce((n, c) => n + c[2], 0);
  const callHours = t.calls.reduce((n, c) => n + c[1], 0) / 3600;
  const tokIn = t.calls.reduce((n, c) => n + c[7], 0), tokOut = t.calls.reduce((n, c) => n + c[6], 0);
  const rs = t.result_sources;
  const modelLabeled = rs.model_call ?? 0;
  const reusedEarlier = (rs.result_cache ?? 0) + (rs.result_cache_duplicate ?? 0);
  const dupThisRun = rs.exact_text_reuse ?? 0;
  const classifiable = modelLabeled + reusedEarlier + dupThisRun;
  const distinct = t.progress.pending_distinct_texts ?? 0;
  const v = ev.data?.verify_summary;
  const full = o.run.scope.full_rows ?? 0;

  const stages: Stage[] = [
    { k: "source", who: "data", title: "Source file", stat: `${fmt(full)} reviews`,
      what: "Google Play reviews of Spotify for Android, May 2022 – November 2023. Every row is read with the course's strict CSV parser and hashed, so no source text can change silently.",
      art: [["grading/ingestion.json", "grading/ingestion.json"]] },
    { k: "ingest", who: "code", title: "Ingest and scope", stat: `${fmt(o.total)} in scope`,
      what: "The course's own seeded sampler, extended to 100,050 nonempty reviews plus all 13 empty ones. The sample contains the golden set and every course sample. Empty texts are quarantined with a reason, never dropped.",
      art: [["subset manifest", "data/subset_100k_manifest.json"], ["ingest.py", "pipeline/ingest.py"]] },
    { k: "cache", who: "code", title: "Result cache", stat: `${fmt(reusedEarlier + dupThisRun)} rows reused`,
      what: `Identical text gets an identical label. ${fmt(classifiable)} rows collapse to ${fmt(distinct)} distinct texts, and labels from the development runs are reused when the text and prompt version match exactly.`,
      art: [["store.py", "pipeline/store.py"]] },
    { k: "enrich", who: "model", title: "Enrichment agent", stat: `${fmt(enrich.length)} batched calls`,
      what: `A 4.6B-parameter open model (Gemma 4, 4-bit) running on the laptop labels up to 50 texts per call: topic, intent, severity 1–5, sentiment and an evidence quote, as compact JSON constrained by a schema. ${fmt(textsSent)} texts in ${fmt(okEnrich.length)} successful calls.`,
      art: [["prompt", "prompts/enrich_v2.md"], ["enrich.py", "pipeline/enrich.py"]] },
    { k: "validate", who: "code", title: "Validator and screens", stat: `${t.invalid_outputs} outputs rejected`,
      what: "Code checks every row: right position in the batch, allowed values only, and a quote that is an exact substring of the review. Rejected rows are retried or split; text that looks like a prompt injection is flagged for human review.",
      art: [["extract.py", "pipeline/extract.py"]] },
    { k: "verify", who: "model", title: "Verifier agent", stat: v ? `${fmt(v.verified)} blind re-labels` : "sampled",
      what: v ? `A separate prompt re-labels a seeded 1% sample without seeing the first answer. Agreement: topic ${(v.topic_agree_rate * 100).toFixed(0)}%, intent ${(v.intent_agree_rate * 100).toFixed(0)}%.` : "A separate prompt re-labels a seeded sample without seeing the first answer.",
      art: [["verify.py", "pipeline/verify.py"]] },
    { k: "rank", who: "code", title: "Rank", stat: `${o.issue_count} issues`,
      what: "Each complaint joins the issue for its subtopic. Priority = complaint count × mean severity; ties break by issue ID. Anyone can recompute it offline from the saved records.",
      art: [["rank.py", "pipeline/rank.py"]] },
    { k: "group", who: "model", title: "Grouping agent", stat: `${byRole(2).length} calls`,
      what: "Names and summarizes each issue from a bounded sample of member quotes. It cannot move a review between issues; code owns membership.",
      art: [["group.py", "pipeline/group.py"]] },
    { k: "memo", who: "model", title: "Memo agent", stat: `${byRole(3).length} calls`,
      what: "Writes the recommendation from code-computed facts, code-computed area orderings and a small evidence pack. It never sees the raw dataset.",
      art: [["prompt", "prompts/memo_v2.md"], ["memo.py", "pipeline/memo.py"]] },
    { k: "check", who: "code", title: "Claim checker", stat: o.memo?.status ?? "–",
      what: "Every number must equal its cited fact, every issue and review ID must exist, every higher-or-lower comparison must hold, and at least two real reviews must be cited. A failed draft gets one revision; a persistent failure is shown, not hidden.",
      art: [["memo_checks.json", "runs/final100k/memo_checks.json"]] },
    { k: "site", who: "code", title: "Database → API → this site", stat: "$0 to browse",
      what: "Saved artifacts load into SQLite, served read-only by FastAPI on Vercel's free tier. Browsing never calls a model.",
      art: [["load_db.py", "dashboard/load_db.py"], ["api", "dashboard/api/index.py"]] },
  ];

  const funnel: [string, number, string][] = [
    ["Reviews in scope", o.total, "declared seeded sample"],
    ["Nonempty, classifiable", classifiable, `${fmt(o.status_counts.quarantined ?? 0)} empty texts quarantined`],
    ["Distinct texts", distinct, "exact duplicates collapse"],
    ["Texts sent to the model", modelLabeled, "after reusing earlier labels"],
  ];
  const sources: [string, number, string][] = [
    ["labeled by a model call in this run", modelLabeled, ACCENT],
    ["reused from an earlier run (same text, same prompt)", reusedEarlier, "#8fb3a7"],
    ["duplicate of a text labeled in this run", dupThisRun, "#c7bda9"],
  ];
  const sc = ev.data?.cost_replay?.projections?.declared_100k_subset?.scenarios;
  const fullSc = ev.data?.cost_replay?.projections?.full_corpus?.scenarios;
  const cap = o.run.budget_usd ?? 5;
  const costRows: [string, number, string][] = sc ? [
    ["This run · local model (measured)", o.run.spend_usd_actual ?? 0, ACCENT],
    ["Claude Haiku · Batch API (modeled)", sc.modeled_alt_haiku_batch_api?.usd.total_api ?? 0, NEUTRAL],
    ["Claude Haiku · standard API (modeled)", sc.modeled_alt_haiku_standard?.usd.total_api ?? 0, NEUTRAL],
  ] : [];
  const maxCost = Math.max(cap, ...costRows.map((r) => r[1])) * 1.08;

  const r = tr.data;
  const memoSentence = (() => {
    const md = rec.data?.memo.markdown ?? "";
    const s = md.replace(/<!--[\s\S]*?-->/g, "").split(/(?<=[.!?])\s+/).find((x) => x.includes(TRACE_ID));
    return s ? s.replace(/`/g, "").replace(TRACE_ID, `${TRACE_ID.slice(0, 8)}…`) : null;
  })();
  const quoteOk = r?.evidence_quote ? (r.review_text ?? "").includes(r.evidence_quote) : null;

  return (
    <>
      <div className="kicker">How it works<span className="dotsep" />Run {o.run.run_id}</div>
      <h1 className="hero" style={{ fontSize: "clamp(40px, 5.4vw, 76px)" }}>
        From {fmt(o.total)} reviews <em>to one recommendation.</em>
      </h1>
      <p className="lede">
        Four model agents read language. Code does everything else: counting, caching, validating, ranking and checking every
        claim. Below is the machinery, a flight recorder of the run that produced this site, and one review traced end to end.
      </p>
      <div className="statstrip">
        <div><div className="v">{fmt(t.calls.length)}</div><div className="l">Model calls</div><div className="s">{enrich.length} enrichment · {byRole(1).length} verify · {byRole(2).length} group · {byRole(3).length} memo</div></div>
        <div><div className="v">{callHours.toFixed(1)} h</div><div className="l">Model time</div><div className="s">one local worker, 8 GB laptop</div></div>
        <div><div className="v">{(textsSent / Math.max(1, okEnrich.length)).toFixed(1)}</div><div className="l">Texts per call</div><div className="s">batched, schema-constrained JSON</div></div>
        <div><div className="v">{(okEnrich.reduce((n, c) => n + c[6], 0) / Math.max(1, textsSent)).toFixed(1)}</div><div className="l">Output tokens per text</div><div className="s">{fmt(tokIn)} in · {fmt(tokOut)} out in total</div></div>
        <div><div className="v">${(o.run.spend_usd_actual ?? 0).toFixed(2)}</div><div className="l">API spend</div><div className="s">cap ${cap.toFixed(2)}</div></div>
      </div>

      <div className="report" style={{ marginTop: 56 }}>
        <Contents items={TOC} />
        <div>
          <Section id="pipeline" n={1} kicker="The pipeline" headline="Models read language; code owns every number"
                   dek="Eleven stages, each handing off saved, inspectable artifacts. Click a stage to see what it does, its numbers in this run and the code behind it."
                   source={<>pipeline run {o.run.run_id}; stage counts read from the saved call log.</>}>
            <div className="flow">
              {stages.map((s, i) => (
                <div key={s.k} className={`flow-row ${open === s.k ? "open" : ""}`}>
                  <button className={`flow-node ${s.who}`} onClick={() => setOpen(open === s.k ? "" : s.k)} aria-expanded={open === s.k}>
                    <span className="flow-dot" />
                    <span className="flow-idx">{String(i + 1).padStart(2, "0")}</span>
                    <span className="flow-title">{s.title}</span>
                    <span className={`who-badge ${s.who}`}>{s.who}</span>
                    <span className="flow-stat">{s.stat}</span>
                  </button>
                  {open === s.k && (
                    <div className="flow-detail">
                      <p>{s.what}</p>
                      <div className="flow-art">{s.art.map(([label, path]) => <a key={path} href={gh(path)} target="_blank" rel="noreferrer">{label} ↗</a>)}</div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Section>

          <Section id="funnel" n={2} kicker="Compute funnel"
                   headline={<>{fmt(classifiable)} labels, {fmt(modelLabeled)} model-labeled texts</>}
                   dek={`All ${fmt(full)} source rows were ingested and profiled; the analysis runs on a declared seeded sample. Caching and deduplication mean only ${((modelLabeled / Math.max(1, classifiable)) * 100).toFixed(0)}% of rows needed a model.`}
                   source={<>run_summary.json result sources and the enrichment progress log.</>}>
            <Card>
              {funnel.map(([label, n, note]) => (
                <div className="funnel-row" key={label}>
                  <div className="funnel-label"><b>{label}</b><span className="muted">{note}</span></div>
                  <div className="funnel-track"><div className="funnel-fill" style={{ width: `${(n / Math.max(1, o.total)) * 100}%` }} /></div>
                  <div className="funnel-n">{fmt(n)}</div>
                </div>
              ))}
              <div className="stack-title">Where each of the {fmt(classifiable)} labels came from</div>
              <div className="stack">
                {sources.map(([label, n, c]) => <div key={label} style={{ width: `${(n / Math.max(1, classifiable)) * 100}%`, background: c }} title={`${label}: ${fmt(n)}`} />)}
              </div>
              <div className="cn-legend">
                {sources.map(([label, n, c]) => <span key={label}><i style={{ background: c, borderRadius: 2 }} />{label} · <b>{fmt(n)}</b></span>)}
              </div>
            </Card>
          </Section>

          <Section id="recorder" n={3} kicker="Flight recorder"
                   headline="The 100K run, call by call — including a planned interruption"
                   dek="Like an agent-trace view: every model call in time order. The run was deliberately stopped with a STOP file and resumed from its checkpoint, later restarted as a detached process, and paused while the laptop slept — no work was lost or repeated."
                   source={<>calls.jsonl ({fmt(t.calls.length)} calls) and run_summary.json invocations; hover a point for its details.</>}>
            <Card><FlightRecorder t={t} /></Card>
          </Section>

          <Section id="trace" n={4} kicker="One review, end to end" headline="Follow a single review from raw text to the memo"
                   dek="This review is cited as evidence in the final recommendation. Every step below is read from saved artifacts."
                   source={<>reviews, issues and memo tables; the full generated trace is in <a href={gh("evals/trace.md")} target="_blank" rel="noreferrer">evals/trace.md</a>.</>}>
            {r ? (
              <ol className="trace">
                <li><div className="trace-k">Source row</div>
                  <blockquote>“{r.review_text}”</blockquote>
                  <div className="muted">rating {r.review_rating} · {r.review_timestamp} · app {r.app_version || "unknown"} · <Link to={`/reviews/${r.review_id}`}>{r.review_id.slice(0, 8)}…</Link></div></li>
                <li><div className="trace-k">Enrichment agent</div>
                  <div className="trace-grid">
                    <span>topic</span><b><span className="dot" style={{ background: areaColor(r.topic) }} />{r.subtopic}</b>
                    <span>intent</span><b>{r.intent}</b>
                    <span>severity</span><b><Sev value={r.severity} /></b>
                    <span>sentiment</span><b>{r.sentiment}</b>
                    <span>source</span><b>{r.result_source}{r.source_request_id ? ` · ${r.source_request_id}` : ""}</b>
                  </div></li>
                <li><div className="trace-k">Validator</div>
                  <div>Evidence quote “<mark>{r.evidence_quote}</mark>” {quoteOk ? <Tag kind="good">exact substring</Tag> : <Tag kind="bad">not found</Tag>}{" "}
                    {r.needs_review ? <Tag kind="warn">flagged: {r.review_reason}</Tag> : <Tag>not flagged</Tag>}</div>
                  <div className="muted mono" style={{ marginTop: 6 }}>{r.label_config}</div></li>
                <li><div className="trace-k">Verifier agent</div>
                  {r.verification ? <div>Blind re-label: {r.verification.verify_topic} · {r.verification.verify_intent} · severity {r.verification.verify_severity} {r.verification.disagreement ? <Tag kind="warn">disagreement</Tag> : <Tag kind="good">agrees</Tag>}</div>
                    : <div className="muted">Not in the seeded 1% verification sample.</div>}</li>
                <li><div className="trace-k">Issue and ranking</div>
                  {r.issue ? <div><Link to={`/issues/${r.issue.issue_id}`}>{r.issue.title}</Link> — rank #{r.issue.rank}, priority {fmt(r.issue.priority_score)}</div> : <div className="muted">Not a complaint; not ranked.</div>}</li>
                <li><div className="trace-k">Memo agent → claim checker</div>
                  {memoSentence ? <blockquote className="memo-q">{memoSentence.replace(/\[F\d+\]/g, "")}</blockquote> : <div className="muted">Loading memo…</div>}
                  <div><Tag kind={o.memo?.status === "checks_passed" ? "good" : "bad"}>{o.memo?.status}</Tag> <Link to="/recommendation">Read the full memo →</Link></div></li>
              </ol>
            ) : <Status loading={tr.loading} error={tr.error} />}
          </Section>

          <Section id="economics" n={5} kicker="Inference economics"
                   headline={<>${(o.run.spend_usd_actual ?? 0).toFixed(2)} in API spend — a paid API would have broken the ${cap.toFixed(0)} cap</>}
                   dek={`Costs for the same 100K scope were modeled from the measured 100-review pilot's real token counts. The local model trades money for time: ${callHours.toFixed(1)} hours of model calls on one laptop, roughly $${(callHours * 0.025 * 0.35).toFixed(2)} of electricity (25 W, $0.35/kWh assumed).`}
                   source={<>cost/report.md and cost/replay_result.json; rerun offline with <code>python3 cost/calculator.py</code>.</>}>
            <Card>
              {costRows.map(([label, usd, c], ri) => (
                <div className="funnel-row" key={label}>
                  <div className="funnel-label"><b>{label}</b></div>
                  <div className="funnel-track cost">
                    <div className="funnel-fill" style={{ width: `${Math.max(0.6, (usd / maxCost) * 100)}%`, background: c }} />
                    <div className="cap" style={{ left: `${(cap / maxCost) * 100}%` }}>{ri === 0 && <span>${cap.toFixed(0)} budget cap</span>}</div>
                  </div>
                  <div className="funnel-n">${usd.toFixed(2)}</div>
                </div>
              ))}
              {fullSc && (
                <p className="muted" style={{ margin: "14px 0 0" }}>
                  For all {fmt(ev.data?.cost_replay?.projections?.full_corpus?.volume.rows)} reviews the same model would cost
                  ${fullSc.modeled_alt_haiku_batch_api?.usd.total_api.toFixed(2)} (Batch API) or ${fullSc.modeled_alt_haiku_standard?.usd.total_api.toFixed(2)} (standard),
                  or about {fullSc.local_base?.local_hours?.toFixed(0)} hours locally.
                </p>
              )}
            </Card>
          </Section>
        </div>
      </div>
    </>
  );
}
