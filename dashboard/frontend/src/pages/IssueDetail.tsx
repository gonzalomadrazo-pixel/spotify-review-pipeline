import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Claim, Issue, Review } from "../api";
import { Card, fmt, Highlight, Pager, ReviewLink, Sev, Stat, Status, Tag, useApi } from "../components";

type Detail = {
  issue: Issue;
  severity_distribution: { severity: number; n: number }[];
  monthly: { month: string; n: number }[];
  claims: Claim[];
  members: Review[];
  page: number;
  page_size: number;
  member_count: number;
};

export default function IssueDetail() {
  const { id = "" } = useParams();
  const [page, setPage] = useState(1);
  const { data, error, loading } = useApi<Detail>(`/api/issues/${encodeURIComponent(id)}?page=${page}&page_size=20`);
  if (!data) return <Status loading={loading} error={error} />;
  const i = data.issue;
  return (
    <>
      <p><Link to="/issues">← Issue ranking</Link></p>
      <h1>{i.title} <span className="muted mono" style={{ fontSize: 14 }}>{i.issue_id}</span></h1>
      <p className="lede">{i.summary}</p>
      <div className="grid kpis">
        <Stat label="Baseline rank" value={`#${i.rank}`} />
        <Stat label="Complaints" value={fmt(i.complaint_count)} />
        <Stat label="Severity sum (priority)" value={fmt(i.severity_sum)} />
        <Stat label="Mean severity" value={Number(i.mean_severity).toFixed(2)} sub={`exact: ${i.mean_severity}`} />
        <Stat label="Severity ≥ 4" value={fmt(i.severe_count)} />
        <Stat label="Cancellation language" value={fmt(i.cancellation_count)} sub="stated intent only" />
      </div>
      <div className="grid two">
        <Card title="Severity of member reviews">
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={data.severity_distribution}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="severity" stroke="var(--muted)" />
              <YAxis stroke="var(--muted)" width={50} />
              <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)" }} />
              <Bar dataKey="n" fill="var(--bar2)" name="reviews" />
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card title="Complaints per month">
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={data.monthly}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
              <XAxis dataKey="month" stroke="var(--muted)" tick={{ fontSize: 11 }} />
              <YAxis stroke="var(--muted)" width={50} />
              <Tooltip contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)" }} />
              <Bar dataKey="n" fill="var(--bar)" name="complaints" />
            </BarChart>
          </ResponsiveContainer>
          <p className="muted">Counts in the sample; first and last months are partial.</p>
        </Card>
      </div>
      {data.claims.length > 0 && (
        <Card title="Memo claims about this issue">
          {data.claims.map((c) => (
            <Tag key={c.claim_id}>{c.claim_id}: {c.metric} = {c.value}</Tag>
          ))}
        </Card>
      )}
      <Card title={`Member reviews (${fmt(data.member_count)})`}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Review</th><th>Sev</th><th>Intent</th><th>Text (evidence quote highlighted)</th><th>Date</th></tr></thead>
            <tbody>
              {data.members.map((r) => (
                <tr key={r.review_id}>
                  <td><ReviewLink id={r.review_id} /></td>
                  <td><Sev value={r.severity} /></td>
                  <td>{r.intent}{r.needs_review && <> <Tag kind="warn">review</Tag></>}</td>
                  <td className="review-text"><Highlight text={r.review_text} quote={r.evidence_quote} /></td>
                  <td className="muted">{r.review_timestamp.slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pager page={page} pageSize={data.page_size} total={data.member_count} onPage={setPage} />
      </Card>
    </>
  );
}
