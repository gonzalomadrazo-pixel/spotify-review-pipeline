import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Issue } from "../api";
import { Card, fmt, IssueLink, Status, useApi } from "../components";

type SortKey = "rank" | "complaint_count" | "mean_severity" | "severe_count" | "cancellation_count";

export default function Issues() {
  const { data, error, loading } = useApi<Issue[]>("/api/issues");
  const [sort, setSort] = useState<SortKey>("rank");
  const [topic, setTopic] = useState("");
  const nav = useNavigate();
  const shown = useMemo(() => {
    const list = (data ?? []).filter((i) => !topic || i.topic === topic);
    if (sort === "rank") return list;
    return [...list].sort((a, b) => Number(b[sort]) - Number(a[sort]) || a.rank - b.rank);
  }, [data, sort, topic]);
  if (!data) return <Status loading={loading} error={error} />;
  const topics = [...new Set(data.map((i) => i.topic))].sort();
  const max = Math.max(1, ...data.map((i) => i.priority_score));

  return (
    <>
      <h1>Issue ranking</h1>
      <p className="lede">
        Required baseline: one issue per complaint or cancellation record (issue = subtopic), <code>priority = complaint_count ×
        mean_severity = severity_sum</code>, ordered by descending score then ascending issue ID. The checker recomputes this from
        the saved records and membership.
      </p>
      <Card
        title={`${fmt(shown.length)} issues`}
        actions={
          <div className="filters" style={{ margin: 0 }}>
            <label>Topic
              <select value={topic} onChange={(e) => setTopic(e.target.value)}>
                <option value="">all</option>
                {topics.map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
            <label>Sort by
              <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
                <option value="rank">baseline rank</option>
                <option value="complaint_count">complaint count</option>
                <option value="mean_severity">mean severity</option>
                <option value="severe_count">severity ≥ 4 count</option>
                <option value="cancellation_count">cancellation language</option>
              </select>
            </label>
          </div>
        }
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Rank</th><th>Issue</th><th>Title</th><th className="num">Complaints</th><th className="num">Severity sum</th>
                <th className="num">Mean</th><th className="num">Sev ≥ 4</th><th className="num">Cancel</th><th style={{ width: "22%" }}>Priority</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((i) => (
                <tr key={i.issue_id} className="clickable" onClick={() => nav(`/issues/${encodeURIComponent(i.issue_id)}`)}>
                  <td>{i.rank}</td>
                  <td><IssueLink id={i.issue_id} /></td>
                  <td>{i.title}</td>
                  <td className="num">{fmt(i.complaint_count)}</td>
                  <td className="num"><b>{fmt(i.severity_sum)}</b></td>
                  <td className="num">{Number(i.mean_severity).toFixed(2)}</td>
                  <td className="num">{fmt(i.severe_count)}</td>
                  <td className="num">{fmt(i.cancellation_count)}</td>
                  <td><div className="bar-track"><div className="bar-fill" style={{ width: `${(i.priority_score / max) * 100}%` }} /></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
