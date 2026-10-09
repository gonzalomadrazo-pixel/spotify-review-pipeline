import { Link, useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Area, Issue } from "../api";
import { Card, Contents, fmt, IssueLink, Section, Sev, Status, Tag, useApi } from "../components";
import Constellation from "../Constellation";
import NeuralSphere from "../NeuralSphere";
import { AREA_BY_KEY, areaColor, areaLabel, AreaKey, chart, SEV_COLORS } from "../theme";
import Trends from "../Trends";

type Overview = {
  run: { run_id: string; generated_at: string; label_config: string; spend_usd_actual: number; budget_usd: number;
    scope: { type?: string; analysis_rows?: number; full_rows?: number } };
  status_counts: Record<string, number>;
  total: number;
  by_topic: { topic: string; reviews: number; complaints: number }[];
  by_intent: { intent: string; n: number }[];
  by_severity: { severity: number; n: number }[];
  mean_sentiment: number | null;
  needs_review: number;
  exact_text_cache_reuse: number;
  quarantine_reasons: { reason: string; n: number }[];
  areas: Area[];
  top_issues: Issue[];
  issue_count: number;
  memo: { status: string; model: string } | null;
  verification: { verified: number; topic_agree_rate: number; intent_agree_rate: number; severity_within_1_rate: number } | null;
  golden: Record<string, unknown> | null;
};
type Rec = { memo: { markdown: string } };

function firstSection(md: string): string {
  const body = md.replace(/<!--[\s\S]*?-->/g, "");
  const m = /##\s*Recommendation\s*\n([\s\S]*?)(\n##\s|$)/i.exec(body);
  return (m ? m[1] : body).replace(/\[F\d+\]/g, "").replace(/[`*]/g, "").trim();
}

const firstSentence = (t: string) => (/^(.+?[.!?])(\s|$)/.exec(t)?.[1] ?? t);
const label = (a: Area) => { const l = areaLabel(a.area); return l.charAt(0).toUpperCase() + l.slice(1); };
const Swatch = ({ area }: { area: string }) => <span className="sw" style={{ background: areaColor(area) }} />;
const TOC: [string, string][] = [
  ["network", "The issue network"], ["recommendation", "AI recommendation"], ["areas", "Where complaints concentrate"],
  ["trend", "Over time"], ["ranking", "Baseline ranking"], ["quality", "Label quality"],
];

export default function OverviewPage() {
  const { data, error, loading } = useApi<Overview>("/api/overview");
  const rec = useApi<Rec>("/api/recommendation");
  const issues = useApi<Issue[]>("/api/issues");
  const nav = useNavigate();
  if (!data) return <Status loading={loading} error={error} />;
  const completed = data.status_counts.completed ?? 0;
  const quarantined = data.status_counts.quarantined ?? 0;
  const complaints = data.by_intent.filter((i) => i.intent === "complaint" || i.intent === "cancellation").reduce((a, b) => a + b.n, 0);
  const cancellations = data.by_intent.find((i) => i.intent === "cancellation")?.n ?? 0;
  const scope = data.run.scope;
  const areaRows = data.areas.filter((a) => a.complaint_count > 0);
  const maxArea = Math.max(1, ...areaRows.map((a) => a.complaint_count));

  // Takeaways are computed from the loaded data, never typed by hand.
  const named = areaRows.filter((a) => a.area !== "other");
  const focus = areaRows.filter((a) => AREA_BY_KEY[a.area as AreaKey]?.focus);
  const largest = [...named].sort((a, b) => b.complaint_count - a.complaint_count)[0];
  const mostSevere = [...focus].sort((a, b) => Number(b.mean_severity) - Number(a.mean_severity))[0];
  const mostBlocking = [...named].sort((a, b) => b.severe_count - a.severe_count)[0];
  const top3 = [...named].sort((a, b) => b.complaint_count - a.complaint_count).slice(0, 3).map((a) => areaLabel(a.area));
  const sevTotal = data.by_severity.reduce((t, s) => t + s.n, 0);
  const blocked = data.by_severity.filter((s) => s.severity >= 4).reduce((t, s) => t + s.n, 0);
  const memoOk = data.memo?.status === "checks_passed";
  const verdict = rec.data ? firstSection(rec.data.memo.markdown) : "";
  const source = `pipeline run ${data.run.run_id}, ${fmt(data.total)} reviews`;

  return (
    <>
      <div className="hero-grid">
        <div>
          <div className="kicker">Research report<span className="dotsep" />Consumer · Product</div>
          <h1 className="hero">
            {fmt(data.total)} reviews. <em>Where should next quarter's product effort go?</em>
          </h1>
          <p className="lede">
            Google Play reviews of Spotify for Android, May 2022 – November 2023, labeled by a model and counted, ranked and
            checked by code.{" "}
            {scope.type === "declared_subset" &&
              `The scope is a seeded random sample of ${fmt(scope.analysis_rows)} of the ${fmt(scope.full_rows)} source reviews; every source row was ingested and profiled.`}
          </p>
          <div className="byline">
            <span>Pipeline run <b>{data.run.run_id}</b></span>
            {data.run.generated_at && <span>Generated {new Date(data.run.generated_at).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })}</span>}
            <span>Model labels · code-checked numbers</span>
          </div>
        </div>
        <div className="sphere-wrap"><NeuralSphere /></div>
      </div>

      <div className="statstrip">
        <div><div className="v">{fmt(data.total)}</div><div className="l">Reviews in scope</div><div className="s">{fmt(completed)} classified · {fmt(quarantined)} quarantined</div></div>
        <div><div className="v">{fmt(complaints)}</div><div className="l">Complaints</div><div className="s">{((complaints / Math.max(1, completed)) * 100).toFixed(1)}% of classified reviews</div></div>
        <div><div className="v">{fmt(cancellations)}</div><div className="l">Cancellation language</div><div className="s">stated intent, not observed churn</div></div>
        <div><div className="v">{fmt(data.issue_count)}</div><div className="l">Issues ranked</div><div className="s"><Link to="/issues">baseline: severity sum</Link></div></div>
        <div><div className="v">{fmt(data.exact_text_cache_reuse)}</div><div className="l">Duplicate texts reused</div><div className="s">labeled once, then cached</div></div>
        <div><div className="v">${(data.run.spend_usd_actual ?? 0).toFixed(2)}</div><div className="l">API spend</div><div className="s">local model · cap ${(data.run.budget_usd ?? 0).toFixed(2)}</div></div>
      </div>

      <div className="kicker takeaways-label">Key takeaways</div>
      <ol className="takeaways">
        {largest && (
          <li><div className="t"><Swatch area={largest.area} />{label(largest)} draws the most complaints</div>
            <div className="d">{fmt(largest.complaint_count)} complaint records, {largest.share_of_complaints} of the total.</div></li>
        )}
        {mostSevere && (
          <li><div className="t"><Swatch area={mostSevere.area} />{label(mostSevere)} complaints are the most severe</div>
            <div className="d">Mean severity {Number(mostSevere.mean_severity).toFixed(2)} of 5, the highest of the four focus areas.</div></li>
        )}
        {mostBlocking && (
          <li><div className="t"><Swatch area={mostBlocking.area} />{label(mostBlocking)} has the most blocking complaints</div>
            <div className="d">{fmt(mostBlocking.severe_count)} complaints at severity 4 or 5.</div></li>
        )}
        <li><div className="t">{memoOk ? "The AI memo's recommendation passes every claim check" : "The AI memo did not pass its claim checks"}</div>
          <div className="d">{verdict ? firstSentence(verdict) : "Loading…"}</div></li>
      </ol>

      <div className="explore">
        <Link to="/how"><span className="ex-k">How it works</span><span className="ex-t">The pipeline, a flight recorder of the run, and one review traced end to end.</span><span className="ex-go">Explore →</span></Link>
        <Link to="/evals"><span className="ex-k">Evals</span><span className="ex-t">How much to trust the labels: human golden set, verifier, red team.</span><span className="ex-go">See the system card →</span></Link>
        <Link to="/story"><span className="ex-k">Build log</span><span className="ex-t">Ten problems we hit, what we tried, and what we threw away.</span><span className="ex-go">Read the story →</span></Link>
      </div>

      <div className="report">
        <Contents items={TOC} />
        <div>
      <Section id="network" n={1} kicker="The issue network"
               headline={<>Complaints concentrate in {top3.slice(0, -1).join(", ")} and {top3[top3.length - 1]}</>}
               dek="Each node is an issue, sized by its complaint count and wired to its product area; signal flows from the full set of complaints. Hover to inspect, click to open the evidence."
               source={<>{source}; issue = subtopic of each complaint or cancellation record. <Link to="/issues">Table view</Link>.</>}>
        <Card className="pool">
          {issues.data ? (
            <Constellation issues={issues.data} areas={data.areas} totalComplaints={complaints} />
          ) : (
            <Status loading={issues.loading} error={issues.error} />
          )}
        </Card>
      </Section>

      <Section id="recommendation" n={2} kicker="AI-generated recommendation" headline="What the memo agent recommends"
               source={<>memo agent ({data.memo?.model}) writing from code-computed facts only; every number and ID is checked by code. <Link to="/recommendation">Full memo, claims and evidence</Link>.</>}>
        <Card className="callout">
          {rec.data ? (
            <>
              <p className="verdict">{verdict}</p>
              <Tag kind={memoOk ? "good" : "bad"}>{data.memo?.status}</Tag>
            </>
          ) : (
            <Status loading={rec.loading} error={rec.error} />
          )}
        </Card>
      </Section>

      <Section id="areas" n={3} kicker="Where complaints concentrate"
               headline={largest ? <>{label(largest)} accounts for {largest.share_of_complaints} of all complaints</> : "Complaints by product area"}
               dek={sevTotal ? `${((blocked / sevTotal) * 100).toFixed(1)}% of classified reviews describe a blocked task or worse (severity 4 or 5).` : undefined}
               source={<>{source}; complaint + cancellation records, billing and support combined as in the brief.</>}>
        <div className="grid two">
          <Card title="Complaints by product area">
            {areaRows.map((a) => {
              const c = areaColor(a.area);
              return (
                <div className="bar-row" key={a.area} title={`mean severity ${a.mean_severity}; ${a.severe_count} with severity ≥ 4`}>
                  <span><span className="dot" style={{ background: c }} />{areaLabel(a.area)}</span>
                  <div className="bar-track"><div className="bar-fill" style={{ width: `${(a.complaint_count / maxArea) * 100}%`, background: c }} /></div>
                  <span className="num">{fmt(a.complaint_count)} · {a.share_of_complaints}</span>
                </div>
              );
            })}
          </Card>
          <Card title="Severity of classified reviews">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={data.by_severity} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid {...chart.grid} />
                <XAxis dataKey="severity" {...chart.axis} tickLine={false} />
                <YAxis {...chart.axis} width={56} tickLine={false} axisLine={false} tickFormatter={(v: number) => fmt(v)} />
                <Tooltip {...chart.tooltip} formatter={(v: number) => [fmt(v), "reviews"]} labelFormatter={(s: number) => `severity ${s}`} />
                <Bar dataKey="n" name="reviews" radius={[4, 4, 0, 0]} maxBarSize={56}>
                  {data.by_severity.map((s) => <Cell key={s.severity} fill={SEV_COLORS[s.severity] || "#857f73"} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            <p className="muted" style={{ fontSize: 12.5, margin: "8px 0 0" }}>
              <Sev value={1} /> no problem · <Sev value={2} /> annoyance · <Sev value={3} /> degraded · <Sev value={4} /> blocked · <Sev value={5} /> financial, privacy or data harm
            </p>
          </Card>
        </div>
      </Section>

      <Section id="trend" n={4} kicker="Over time" headline="Complaint share by month, four focus areas"
               dek="Complaints per 100 reviews in each month. The first and last months are partial."
               source={<>{source}; comparable-period figures (June–October 2022 vs 2023) are in the memo's facts table.</>}>
        <Trends />
      </Section>

      <Section id="ranking" n={5} kicker="Baseline ranking" headline={`The top ${data.top_issues.length} issues by severity sum`}
               dek="Priority = complaint count × mean severity, ties broken by issue ID. The course checker recomputes it from saved records."
               source={<>{source}. <Link to="/issues">All {fmt(data.issue_count)} issues</Link>.</>}>
        <Card>
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>Issue</th><th>Title</th><th className="num">Complaints</th><th className="num">Mean severity</th><th className="num">Priority</th></tr></thead>
              <tbody>
                {data.top_issues.map((i) => (
                  <tr key={i.issue_id} className="clickable" onClick={() => nav(`/issues/${encodeURIComponent(i.issue_id)}`)}>
                    <td className="num" style={{ textAlign: "left" }}>{String(i.rank).padStart(2, "0")}</td>
                    <td style={{ whiteSpace: "nowrap" }}><span className="dot" style={{ background: areaColor(i.topic) }} /><IssueLink id={i.issue_id} /></td>
                    <td>{i.title}</td>
                    <td className="num">{fmt(i.complaint_count)}</td><td className="num">{Number(i.mean_severity).toFixed(2)}</td>
                    <td className="num"><b>{fmt(i.priority_score)}</b></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </Section>

      <Section id="quality" n={6} kicker="Label quality" headline="Model labels, checked two ways"
               source={<>{source}; verifier sample and golden-set details on <Link to="/method">Method & evidence</Link>.</>}>
        <div className="grid two">
          <Card title="Independent verification">
            {data.verification ? (
              <div className="statstrip" style={{ margin: 0 }}>
                <div><div className="v">{(data.verification.topic_agree_rate * 100).toFixed(0)}%</div><div className="l">Topic agreement</div></div>
                <div><div className="v">{(data.verification.intent_agree_rate * 100).toFixed(0)}%</div><div className="l">Intent agreement</div></div>
                <div><div className="v">{(data.verification.severity_within_1_rate * 100).toFixed(0)}%</div><div className="l">Severity within one level</div></div>
              </div>
            ) : <p className="muted">No verifier results in this run.</p>}
            <p className="muted" style={{ marginBottom: 0 }}>
              {data.verification && <>A separate verifier re-labeled {fmt(data.verification.verified)} sampled reviews without seeing the first answer. </>}
              {fmt(data.needs_review)} labels are flagged for human review.
            </p>
          </Card>
          <Card title="Intent mix">
            {data.by_intent.map((i) => (
              <div className="bar-row" key={i.intent}>
                <span>{i.intent}</span>
                <div className="bar-track"><div className="bar-fill" style={{ width: `${(i.n / Math.max(1, completed)) * 100}%` }} /></div>
                <span className="num">{fmt(i.n)}</span>
              </div>
            ))}
            {data.quarantine_reasons.length > 0 && (
              <p className="muted" style={{ marginBottom: 0 }}>Quarantined: {data.quarantine_reasons.map((q) => `${q.reason} (${q.n})`).join(", ")}</p>
            )}
          </Card>
        </div>
      </Section>
        </div>
      </div>
    </>
  );
}
