import { Link, useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Area, Issue } from "../api";
import { Card, fmt, IssueLink, Sev, Stat, Status, Tag, useApi } from "../components";

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

const SEV_COLORS = ["", "var(--sev1)", "var(--sev2)", "var(--sev3)", "var(--sev4)", "var(--sev5)"];
const AREA_LABEL: Record<string, string> = { billing_support: "billing / support" };

function firstSection(md: string): string {
  const body = md.replace(/<!--[\s\S]*?-->/g, "");
  const m = /##\s*Recommendation\s*\n([\s\S]*?)(\n##\s|$)/i.exec(body);
  return (m ? m[1] : body).replace(/\[F\d+\]/g, "").replace(/[`*]/g, "").trim();
}

export default function OverviewPage() {
  const { data, error, loading } = useApi<Overview>("/api/overview");
  const rec = useApi<Rec>("/api/recommendation");
  const nav = useNavigate();
  if (!data) return <Status loading={loading} error={error} />;
  const completed = data.status_counts.completed ?? 0;
  const quarantined = data.status_counts.quarantined ?? 0;
  const complaints = data.by_intent.filter((i) => i.intent === "complaint" || i.intent === "cancellation").reduce((a, b) => a + b.n, 0);
  const cancellations = data.by_intent.find((i) => i.intent === "cancellation")?.n ?? 0;
  const scope = data.run.scope;
  const areaRows = data.areas.filter((a) => a.complaint_count > 0);
  const maxArea = Math.max(1, ...areaRows.map((a) => a.complaint_count));

  return (
    <>
      <h1>Spotify Android reviews, May 2022 – Nov 2023</h1>
      <p className="lede">
        Every number on this site is read from the database loaded with pipeline run <code>{data.run.run_id}</code>. Labels come
        from a model; counts, rankings and claim checks are computed by code.{" "}
        {scope.type === "declared_subset" &&
          `Scope: a seeded random sample of ${fmt(scope.analysis_rows)} of the ${fmt(scope.full_rows)} source reviews (all source rows were ingested and profiled).`}
      </p>

      <div className="grid kpis">
        <Stat label="Reviews in scope" value={fmt(data.total)} sub={`${fmt(completed)} classified · ${fmt(quarantined)} quarantined`} />
        <Stat label="Complaints" value={fmt(complaints)} sub={`${((complaints / Math.max(1, completed)) * 100).toFixed(1)}% of classified reviews`} />
        <Stat label="Cancellation language" value={fmt(cancellations)} sub="stated intent, not observed churn" />
        <Stat label="Issues ranked" value={fmt(data.issue_count)} sub={<Link to="/issues">baseline: severity sum</Link>} />
        <Stat label="Exact-text cache reuse" value={fmt(data.exact_text_cache_reuse)} sub="duplicate reviews labeled once" />
        <Stat label="API spend" value={`$${(data.run.spend_usd_actual ?? 0).toFixed(2)}`} sub={`cap $${(data.run.budget_usd ?? 0).toFixed(2)} · local model`} />
      </div>

      <Card title="AI-generated recommendation" actions={<Link to="/recommendation">Full memo, claims and evidence →</Link>}>
        {rec.data ? (
          <>
            <p style={{ fontSize: 15, maxWidth: 900 }}>{firstSection(rec.data.memo.markdown)}</p>
            <p className="muted">
              Written by the memo agent ({data.memo?.model}) from saved aggregates only. Automated claim check:{" "}
              <Tag kind={data.memo?.status === "checks_passed" ? "good" : "bad"}>{data.memo?.status}</Tag>
            </p>
          </>
        ) : (
          <Status loading={rec.loading} error={rec.error} />
        )}
      </Card>

      <div className="grid two">
        <Card title="Complaints by product area">
          {areaRows.map((a) => (
            <div className="bar-row" key={a.area} title={`mean severity ${a.mean_severity}; ${a.severe_count} with severity ≥ 4`}>
              <span>{AREA_LABEL[a.area] ?? a.area}</span>
              <div className="bar-track"><div className="bar-fill" style={{ width: `${(a.complaint_count / maxArea) * 100}%` }} /></div>
              <span className="num">{fmt(a.complaint_count)} · {a.share_of_complaints}</span>
            </div>
          ))}
          <p className="muted" style={{ marginTop: 10 }}>Complaint + cancellation records; billing and support are combined as in the brief.</p>
        </Card>
        <Card title="Severity of classified reviews">
          <ResponsiveContainer width="100%" height={210}>
            <BarChart data={data.by_severity}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="severity" stroke="var(--muted)" />
              <YAxis stroke="var(--muted)" width={60} />
              <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)" }} />
              <Bar dataKey="n" name="reviews">
                {data.by_severity.map((s) => <Cell key={s.severity} fill={SEV_COLORS[s.severity] || "var(--bar)"} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <p className="muted">1 = no problem · 2 = annoyance · 3 = degraded · 4 = blocked · 5 = financial/privacy/data harm</p>
        </Card>
      </div>

      <Card title="Top issues (baseline ranking)" actions={<Link to="/issues">All issues →</Link>}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>#</th><th>Issue</th><th>Title</th><th className="num">Complaints</th><th className="num">Mean severity</th><th className="num">Priority (severity sum)</th></tr></thead>
            <tbody>
              {data.top_issues.map((i) => (
                <tr key={i.issue_id} className="clickable" onClick={() => nav(`/issues/${encodeURIComponent(i.issue_id)}`)}>
                  <td>{i.rank}</td><td><IssueLink id={i.issue_id} /></td><td>{i.title}</td>
                  <td className="num">{fmt(i.complaint_count)}</td><td className="num">{Number(i.mean_severity).toFixed(2)}</td>
                  <td className="num"><b>{fmt(i.priority_score)}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid two">
        <Card title="Label quality checks">
          {data.verification ? (
            <p>
              Independent verifier re-labeled {fmt(data.verification.verified)} sampled reviews without seeing the first answer: topic agreement{" "}
              <b>{(data.verification.topic_agree_rate * 100).toFixed(0)}%</b>, intent{" "}
              <b>{(data.verification.intent_agree_rate * 100).toFixed(0)}%</b>, severity within one level{" "}
              <b>{(data.verification.severity_within_1_rate * 100).toFixed(0)}%</b>.
            </p>
          ) : <p className="muted">No verifier results in this run.</p>}
          <p className="muted">{data.golden ? "Golden-set (human label) results: see Method & evidence." : "Golden-set evaluation not loaded yet."}{" "}
            {fmt(data.needs_review)} labels are flagged for human review.</p>
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
            <p className="muted">Quarantined: {data.quarantine_reasons.map((q) => `${q.reason} (${q.n})`).join(", ")}</p>
          )}
        </Card>
      </div>
      <p className="muted">Severity legend: <Sev value={1} /> <Sev value={2} /> <Sev value={3} /> <Sev value={4} /> <Sev value={5} /></p>
    </>
  );
}
