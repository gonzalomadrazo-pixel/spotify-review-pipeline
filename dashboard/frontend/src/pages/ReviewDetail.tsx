import { Link, useParams } from "react-router-dom";
import { Review } from "../api";
import { Card, fmt, Highlight, IssueLink, ReviewLink, Sev, SEVERITY, Status, Tag, useApi } from "../components";

type Detail = Review & {
  verification: { verify_topic: string; verify_intent: string; verify_severity: number; disagreement: number } | null;
  issue: { issue_id: string; rank: number; title: string; priority_score: number } | null;
  cache_source?: { review_id: string } | null;
};

export default function ReviewDetail() {
  const { id = "" } = useParams();
  const { data: r, error, loading } = useApi<Detail>(`/api/reviews/${encodeURIComponent(id)}`);
  if (!r) return <Status loading={loading} error={error} />;
  return (
    <>
      <p><Link to="/reviews">← Reviews</Link></p>
      <div className="eyebrow">Review · full provenance</div>
      <h1>Review <span className="mono muted" style={{ fontSize: 14 }}>{r.review_id}</span></h1>
      <Card title="Original text (source value, unchanged)">
        <p className="review-text" style={{ fontSize: 15 }}><Highlight text={r.review_text || "(empty review text)"} quote={r.evidence_quote} /></p>
        <p className="muted">
          {r.review_timestamp} · {r.review_rating} stars (metadata only; never used as a label) · {fmt(Number(r.review_likes))} likes ·
          app version {r.app_version || "missing"}
        </p>
      </Card>
      <div className="grid two">
        <Card title="Enrichment (model labels, validated by code)">
          {r.status === "completed" ? (
            <dl className="kv">
              <dt>Topic / subtopic</dt><dd>{r.topic} · <span className="mono">{r.subtopic}</span></dd>
              <dt>Intent</dt><dd>{r.intent}</dd>
              <dt>Severity</dt><dd><Sev value={r.severity} /> {r.severity ? SEVERITY[r.severity] : ""}</dd>
              <dt>Sentiment (−1 to 1)</dt><dd>{r.sentiment}</dd>
              <dt>Evidence quote</dt><dd>“{r.evidence_quote}”</dd>
              <dt>Entities (code-extracted)</dt><dd>{r.entities.length ? r.entities.map((e) => <Tag key={e}>{e}</Tag>) : "none"}</dd>
              <dt>Needs review</dt><dd>{r.needs_review ? <Tag kind="warn">{r.review_reason}</Tag> : "no"}</dd>
            </dl>
          ) : (
            <p><Tag kind="bad">quarantined</Tag> reason: <code>{r.reason}</code></p>
          )}
        </Card>
        <Card title="Provenance">
          <dl className="kv">
            <dt>label_config</dt><dd className="mono">{r.label_config ?? "–"}</dd>
            <dt>Result source</dt><dd>{r.result_source ?? "–"}</dd>
            <dt>Model call</dt><dd className="mono">{r.source_request_id ?? "–"}</dd>
            <dt>Exact-text cache</dt><dd>{r.cache_source_id ? <>reused from <ReviewLink id={r.cache_source_id} /></> : "labeled directly"}</dd>
            <dt>Issue membership</dt><dd>{r.issue ? <><IssueLink id={r.issue.issue_id} /> (rank #{r.issue.rank}, priority {fmt(r.issue.priority_score)})</> : "not in a complaint issue"}</dd>
          </dl>
        </Card>
      </div>
      {r.verification && (
        <Card title="Independent verification">
          <p>
            The verifier re-labeled this review without seeing the enrichment: topic <b>{r.verification.verify_topic}</b>, intent{" "}
            <b>{r.verification.verify_intent}</b>, severity <Sev value={r.verification.verify_severity} />.{" "}
            {r.verification.disagreement ? <Tag kind="bad">disagreement</Tag> : <Tag kind="good">agrees</Tag>}
          </p>
        </Card>
      )}
    </>
  );
}
