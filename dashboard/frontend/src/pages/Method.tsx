import { Card, fmt, Stat, Status, Tag, useApi } from "../components";

type Calls = Record<string, { attempts: number; succeeded: number; failed: number; retries: number; input_tokens: number;
  cache_read_input_tokens: number; output_tokens: number; cost_usd: number; review_ids_sent: number }>;
type Evidence = {
  run_summary: { run_id: string; budget_usd: number; spend_usd_actual: number; coverage: Record<string, unknown>;
    result_sources: Record<string, number>; calls_by_role_model_tier: Calls;
    invocations: { n: number; phase: string; stop_reason: string; completed_before: number; completed_after: number }[] } | null;
  verify_summary?: Record<string, number | string>;
  ingestion_report?: { scope: Record<string, unknown>; official_profile: { file_sha256: string; counts: Record<string, number> };
    accounting: Record<string, number> };
  golden_summary?: Record<string, unknown>;
  injection_results?: { passed: number; cases: number };
  planted_errors?: { planted: number; flagged: number };
  cost_replay?: { measured: Record<string, { wall_clock_s: number; api_usd: number; enrichment_calls: number }> };
};

const STAGES: [string, string, string][] = [
  ["1. Prepare", "code", "Parse all 660,622 rows, hash every source row, profile, find exact duplicate texts, queue pending work."],
  ["2. Enrich", "model", "Local model labels ≤50 distinct texts per request; code validates keys, enums, quotes and saves each batch atomically."],
  ["3. Verify", "model", "A separate prompt re-labels a seeded sample without seeing the first answer; code compares."],
  ["4. Group", "code + model", "Code assigns each complaint to its subtopic issue; a model only names issues from bounded examples."],
  ["5. Rank", "code", "priority = complaint_count × mean_severity = severity_sum; ties by issue ID."],
  ["6. Recommend", "model", "Memo agent sees only saved aggregates + evidence; code checks every number and ID."],
];

export default function Method() {
  const { data, error, loading } = useApi<Evidence>("/api/evidence");
  if (!data) return <Status loading={loading} error={error} />;
  const s = data.run_summary;
  const ing = data.ingestion_report;
  const v = data.verify_summary;
  const pilot = data.cost_replay?.measured;
  return (
    <>
      <div className="eyebrow">Method · how every number was made</div>
      <h1>Method & evidence</h1>
      <p className="lede">How the numbers were produced, and the checks that back them. Code owns record accounting and arithmetic; models only read language.</p>
      <Card title="Pipeline (six stages)">
        <div className="pipeline">
          {STAGES.map(([name, who, what]) => (
            <div className="stage" key={name}>
              <span className={`who ${who.includes("model") ? "model" : "code"}`}>{who}</span>
              <b>{name}</b>
              <span className="muted">{what}</span>
            </div>
          ))}
        </div>
        <p className="muted" style={{ marginTop: 10 }}>Data flow: pipeline run → saved artifacts in <code>runs/&lt;run_id&gt;/</code> → <code>dashboard/load_db.py</code> → SQLite database (deployed with the backend) → read-only FastAPI backend → this dashboard. No model is called when you browse.</p>
      </Card>
      {ing && (
        <Card title="Ingestion and scope">
          <div className="grid kpis">
            <Stat label="Source rows profiled" value={fmt(ing.official_profile.counts.records)}
              sub={ing.scope?.type === "declared_subset" ? "entire source file" : "input file of this run"} />
            <Stat label="Empty texts" value={fmt(ing.official_profile.counts.empty_review_text)} sub="quarantined: empty_review_text" />
            <Stat label="Missing app version" value={fmt(ing.official_profile.counts.missing_app_version)} />
            <Stat label="Rows in analysis scope" value={fmt(ing.accounting.rows)} sub={`${fmt(ing.accounting.distinct_nonempty_texts)} distinct texts`} />
          </div>
          <p className="muted mono">source sha256 {ing.official_profile.file_sha256}</p>
        </Card>
      )}
      {s && (
        <Card title={`Run ${s.run_id}: calls, usage and spend`}>
          <p>API spend <b>${s.spend_usd_actual.toFixed(4)}</b> of a ${s.budget_usd.toFixed(2)} cap. Result sources: {Object.entries(s.result_sources).map(([k, n]) => <Tag key={k}>{k}: {fmt(n)}</Tag>)}</p>
          <div className="table-wrap">
            <table>
              <thead><tr><th>role | model | tier</th><th className="num">attempts</th><th className="num">ok</th><th className="num">failed</th><th className="num">retries</th><th className="num">input tok</th><th className="num">cached in</th><th className="num">output tok</th><th className="num">USD</th></tr></thead>
              <tbody>
                {Object.entries(s.calls_by_role_model_tier).map(([k, c]) => (
                  <tr key={k}><td className="mono">{k}</td><td className="num">{fmt(c.attempts)}</td><td className="num">{fmt(c.succeeded)}</td><td className="num">{fmt(c.failed)}</td><td className="num">{fmt(c.retries)}</td><td className="num">{fmt(c.input_tokens)}</td><td className="num">{fmt(c.cache_read_input_tokens)}</td><td className="num">{fmt(c.output_tokens)}</td><td className="num">${c.cost_usd.toFixed(4)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3 style={{ fontSize: 14 }}>Invocations (interruption / resume evidence)</h3>
          <table>
            <thead><tr><th>#</th><th>phase</th><th>stop reason</th><th className="num">completed before</th><th className="num">completed after</th></tr></thead>
            <tbody>{s.invocations.map((i) => <tr key={i.n}><td>{i.n}</td><td>{i.phase}</td><td>{i.stop_reason}</td><td className="num">{fmt(i.completed_before)}</td><td className="num">{fmt(i.completed_after)}</td></tr>)}</tbody>
          </table>
        </Card>
      )}
      <div className="grid two">
        <Card title="Independent verification">
          {v ? (
            <dl className="kv">
              <dt>Sample</dt><dd>{fmt(Number(v.verified))} of {fmt(Number(v.sample_size))} verified</dd>
              <dt>Topic agreement</dt><dd>{(Number(v.topic_agree_rate) * 100).toFixed(1)}%</dd>
              <dt>Intent agreement</dt><dd>{(Number(v.intent_agree_rate) * 100).toFixed(1)}%</dd>
              <dt>Severity exact / ±1</dt><dd>{(Number(v.severity_exact_rate) * 100).toFixed(1)}% / {(Number(v.severity_within_1_rate) * 100).toFixed(1)}%</dd>
              <dt>Disagreements</dt><dd>{fmt(Number(v.disagreements))}</dd>
              <dt>Rule</dt><dd className="muted">{String(v.sample_rule)}</dd>
            </dl>
          ) : <p className="muted">Not run.</p>}
        </Card>
        <Card title="System tests">
          <dl className="kv">
            <dt>Planted errors</dt><dd>{data.planted_errors ? `${data.planted_errors.flagged} of ${data.planted_errors.planted} flagged` : "not loaded"}</dd>
            <dt>Injection cases</dt><dd>{data.injection_results ? `${data.injection_results.passed} of ${data.injection_results.cases} passed` : "not loaded"}</dd>
            <dt>Golden set (50 human labels)</dt><dd>{data.golden_summary ? <Tag kind="good">loaded</Tag> : <Tag kind="warn">pending human labels</Tag>}</dd>
            <dt>100-review pilot</dt><dd>{pilot ? `cold ${pilot.cold.wall_clock_s.toFixed(0)} s / $${pilot.cold.api_usd.toFixed(2)}; warm ${pilot.warm.wall_clock_s.toFixed(1)} s with ${pilot.warm.enrichment_calls} enrichment calls` : "not loaded"}</dd>
          </dl>
        </Card>
      </div>
      <Card title="Limitations">
        <ul>
          <li>Self-selected public Google Play reviews: not a representative customer population; no revenue, plan tier or confirmed churn.</li>
          <li>Cancellation language is stated intent, not observed cancellation.</li>
          <li>Labels come from a small local model and have measured error (see verification and golden-set results).</li>
          <li>Scope is a declared seeded sample of the source file; all source rows were ingested and profiled.</li>
        </ul>
      </Card>
    </>
  );
}
