import { Claim, Fact } from "../api";
import { Card, IssueLink, Markdown, Status, Tag, useApi } from "../components";

type Rec = {
  memo: { markdown: string; status: string; model: string; label_config: string | null };
  checks: { status: string; check: { ok: boolean; errors: string[] } | null; revisions: number; supplied_issue_ids: string[] | null };
  facts: Fact[];
  cited_fact_ids: string[];
  claims: Claim[];
};

export default function Recommendation() {
  const { data, error, loading } = useApi<Rec>("/api/recommendation");
  if (!data) return <Status loading={loading} error={error} />;
  const facts = Object.fromEntries(data.facts.map((f) => [f.fact_id, f]));
  const issueIds = new Set(data.checks.supplied_issue_ids ?? []);
  const cited = data.facts.filter((f) => data.cited_fact_ids.includes(f.fact_id));
  const ok = data.checks.status === "checks_passed";
  return (
    <>
      <h1>AI-generated recommendation</h1>
      <p className="lede">
        The memo agent (<code>{data.memo.model}</code>) received only code-computed aggregates (the FACTS table), the top issues and a
        bounded evidence pack, never the raw dataset. Code then checked every number against its cited fact, and every issue and
        review ID against what was supplied. <Tag kind={ok ? "good" : "bad"}>{data.checks.status}</Tag>
        {data.checks.revisions > 1 && <Tag kind="warn">{data.checks.revisions} drafts</Tag>}
      </p>
      {!ok && data.checks.check && (
        <div className="notice error">Automated check problems: {data.checks.check.errors.join("; ")}</div>
      )}
      <Card>
        <Markdown source={data.memo.markdown} facts={facts} issueIds={issueIds} />
      </Card>
      <Card title={`Cited facts (${cited.length})`}>
        <p className="muted">Each [F##] in the memo links here. Issue-level facts are exported to <code>claims.csv</code>, and the course checker recomputes them from saved records and membership.</p>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Fact</th><th>Scope</th><th>Subject</th><th>Metric</th><th className="num">Value</th><th>Formula</th></tr></thead>
            <tbody>
              {cited.map((f) => (
                <tr key={f.fact_id} id={`fact-${f.fact_id}`}>
                  <td className="mono">{f.fact_id}</td>
                  <td>{f.scope}</td>
                  <td>{f.scope === "issue" ? <IssueLink id={f.subject} /> : f.subject}</td>
                  <td>{f.metric}</td>
                  <td className="num"><b>{f.display}</b></td>
                  <td className="muted">{f.formula}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
