import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { qs, Review } from "../api";
import { Card, fmt, Highlight, IssueLink, Pager, Sev, Status, Tag, useApi } from "../components";

const TOPICS = ["access", "usability", "playback", "downloads", "catalog", "billing", "support", "other"];
const INTENTS = ["cancellation", "complaint", "request", "praise", "unclear"];
type Page = { total: number; page: number; page_size: number; items: Review[] };

export default function Reviews() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const nav = useNavigate();
  const page = Number(params.get("page") ?? 1);
  const filters = {
    topic: params.get("topic") ?? "", intent: params.get("intent") ?? "", severity: params.get("severity") ?? "",
    status: params.get("status") ?? "", needs_review: params.get("needs_review") ?? "", q: params.get("q") ?? "",
  };
  const { data, error, loading } = useApi<Page>(`/api/reviews${qs({ ...filters, page, page_size: 25 })}`);
  useEffect(() => setQ(params.get("q") ?? ""), [params]);
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    next.delete("page");
    setParams(next);
  };
  const select = (key: keyof typeof filters, options: string[], label: string) => (
    <label>{label}
      <select value={filters[key]} onChange={(e) => set(key, e.target.value)}>
        <option value="">all</option>
        {options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );

  return (
    <>
      <h1>Reviews</h1>
      <p className="lede">Every review in scope with its saved labels. Filters run as database queries in the backend.</p>
      <div className="filters">
        {select("topic", TOPICS, "Topic")}
        {select("intent", INTENTS, "Intent")}
        {select("severity", ["1", "2", "3", "4", "5"], "Severity")}
        {select("status", ["completed", "quarantined"], "Status")}
        {select("needs_review", ["true", "false"], "Flagged for review")}
        <label>Search text
          <form onSubmit={(e) => { e.preventDefault(); set("q", q.trim()); }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. shuffle" />
          </form>
        </label>
      </div>
      <Status loading={loading && !data} error={error} />
      {data && (
        <Card title={`${fmt(data.total)} matching reviews`}>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Topic / subtopic</th><th>Intent</th><th>Sev</th><th>Sent.</th><th>Review</th><th>Issue</th></tr></thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.review_id} className="clickable" onClick={() => nav(`/reviews/${encodeURIComponent(r.review_id)}`)}>
                    <td>{r.status === "completed" ? <>{r.topic}<div className="muted mono">{r.subtopic}</div></> : <Tag kind="bad">{r.reason}</Tag>}</td>
                    <td>{r.intent}{r.needs_review && <div><Tag kind="warn">review</Tag></div>}</td>
                    <td><Sev value={r.severity} /></td>
                    <td className="num">{r.sentiment ?? "–"}</td>
                    <td className="review-text" style={{ maxWidth: 560 }}><Highlight text={r.review_text || "(empty)"} quote={r.evidence_quote} /></td>
                    <td onClick={(e) => e.stopPropagation()}>{r.issue_id ? <IssueLink id={r.issue_id} /> : <span className="muted">–</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager page={page} pageSize={data.page_size} total={data.total} onPage={(p) => { const n = new URLSearchParams(params); n.set("page", String(p)); setParams(n); }} />
        </Card>
      )}
    </>
  );
}
