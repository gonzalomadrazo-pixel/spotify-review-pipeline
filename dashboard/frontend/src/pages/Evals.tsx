import { Link } from "react-router-dom";
import { Card, Contents, fmt, gh, Section, Status, Tag, useApi } from "../components";
import { ACCENT } from "../theme";

type Confusion = { labels: string[]; rows: { human: string; counts: number[] }[] };
type Injection = { id: string; purpose: string; text: string; expected: Record<string, unknown[]>; predicted: { topic: string; intent: string; severity: number };
  label_correct: boolean; screen_flagged: boolean; control: boolean };
type Evidence = {
  verify_summary?: { verified: number; topic_agree_rate: number; intent_agree_rate: number; severity_exact_rate: number; severity_within_1_rate: number; disagreements: number };
  golden_summary?: { cases: number; agreement: Record<string, number>; severity_within_1: number; severity_mae: number; sentiment_mae: number;
    evidence_quote_exact_substring_rate: number; topic_macro_f1: number; intent_macro_f1: number };
  golden_detail?: { topic?: Confusion; intent?: Confusion };
  injection_detail?: Injection[];
  planted_errors?: { planted: number; flagged: number; cases: { planted_field: string; flagged_by_comparison: boolean; original: Record<string, unknown>; planted: Record<string, unknown> }[] };
};
type Rec = { checks: { status: string; revisions: number } };

const TOC: [string, string][] = [["scorecard", "Scorecard"], ["golden", "Human golden set"], ["redteam", "Red team"], ["planted", "Planted errors"], ["claims", "Claim checks"]];
const pct = (x?: number) => (x == null ? "–" : `${(x * 100).toFixed(0)}%`);

function Heatmap({ c, title }: { c: Confusion; title: string }) {
  const max = Math.max(1, ...c.rows.flatMap((r) => r.counts));
  return (
    <div className="heat">
      <div className="stack-title">{title}</div>
      <div className="table-wrap">
        <table className="heat-table">
          <thead><tr><th>human ↓ · model →</th>{c.labels.map((l) => <th key={l} className="num">{l}</th>)}</tr></thead>
          <tbody>
            {c.rows.map((r) => (
              <tr key={r.human}>
                <th>{r.human}</th>
                {r.counts.map((n, j) => {
                  const a = n / max;
                  const diag = c.labels[j] === r.human;
                  return (
                    <td key={j} className={`num cell${diag ? " diag" : ""}`} style={{ background: n ? `rgba(51,109,93,${0.08 + a * 0.82})` : undefined, color: a > 0.5 ? "#fff" : undefined }}
                        title={`human ${r.human} · model ${c.labels[j]}: ${n}`}>{n || ""}</td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function Evals() {
  const { data, error, loading } = useApi<Evidence>("/api/evidence");
  const rec = useApi<Rec>("/api/recommendation");
  if (!data) return <Status loading={loading} error={error} />;
  const v = data.verify_summary, g = data.golden_summary, inj = data.injection_detail ?? [], pl = data.planted_errors;
  const attacks = inj.filter((x) => !x.control), controls = inj.filter((x) => x.control);
  const rows: [string, string, string, number | null, string][] = [
    ["Independent verifier · topic", `Blind re-label of a seeded 1% sample (n = ${fmt(v?.verified)})`, pct(v?.topic_agree_rate), v?.topic_agree_rate ?? null, "agreement"],
    ["Independent verifier · intent", "Same sample, intent field", pct(v?.intent_agree_rate), v?.intent_agree_rate ?? null, "agreement"],
    ["Independent verifier · severity ±1", "Same sample, severity within one level", pct(v?.severity_within_1_rate), v?.severity_within_1_rate ?? null, "agreement"],
    ["Golden set · intent", `Model vs ${g?.cases ?? 50} human labels, never shown to a model`, pct(g?.agreement.intent), g?.agreement.intent ?? null, "agreement"],
    ["Golden set · severity ±1", "Same 50, severity within one level", pct(g?.severity_within_1), g?.severity_within_1 ?? null, "agreement"],
    ["Golden set · topic", "Same 50; most misses are the 'other' boundary", pct(g?.agreement.topic), g?.agreement.topic ?? null, "agreement"],
    ["Evidence quotes", "Quote is an exact substring of the review (golden set)", pct(g?.evidence_quote_exact_substring_rate), g?.evidence_quote_exact_substring_rate ?? null, "rate"],
    ["Planted errors", "Corrupted labels the code comparison must flag", pl ? `${pl.flagged} / ${pl.planted}` : "–", pl ? pl.flagged / Math.max(1, pl.planted) : null, "caught"],
    ["Injection screen · attacks", "Synthetic prompt-injection reviews flagged by code", `${attacks.filter((x) => x.screen_flagged).length} / ${attacks.length}`, attacks.length ? attacks.filter((x) => x.screen_flagged).length / attacks.length : null, "caught"],
    ["Injection screen · controls", "Clean control reviews wrongly flagged (lower is better)", `${controls.filter((x) => x.screen_flagged).length} / ${controls.length}`, controls.length ? 1 - controls.filter((x) => x.screen_flagged).length / controls.length : null, "clean"],
    ["Model alone vs injection", "Injected reviews the model still labeled correctly", `${attacks.filter((x) => x.label_correct).length} / ${attacks.length}`, attacks.length ? attacks.filter((x) => x.label_correct).length / attacks.length : null, "robust"],
  ];

  return (
    <>
      <div className="kicker">Evals<span className="dotsep" />System card</div>
      <h1 className="hero" style={{ fontSize: "clamp(40px, 5.4vw, 76px)" }}>How much to trust <em>the labels.</em></h1>
      <p className="lede">
        A small local model labeled these reviews, so its errors are measured, not assumed: against 50 human labels, against an
        independent verifier, against deliberately planted mistakes, and against prompt-injection attacks. The weak spots are
        shown alongside the strong ones.
      </p>

      <div className="report" style={{ marginTop: 40 }}>
        <Contents items={TOC} />
        <div>
          <Section id="scorecard" n={1} kicker="Scorecard" headline="Every evaluation, in one table"
                   source={<>verify_summary.json, evals/golden/, evals/planted_errors.json, evals/injection/results.json.</>}>
            <Card>
              <div className="table-wrap">
                <table className="score">
                  <thead><tr><th>Evaluation</th><th>What it tests</th><th className="num">Result</th><th style={{ width: "26%" }} /></tr></thead>
                  <tbody>
                    {rows.map(([name, what, res, frac]) => (
                      <tr key={name}>
                        <td><b>{name}</b></td><td className="muted">{what}</td><td className="num score-v">{res}</td>
                        <td>{frac != null && <div className="bar-track"><div className="bar-fill" style={{ width: `${frac * 100}%`, background: frac >= 0.75 ? ACCENT : frac >= 0.5 ? "#8fb3a7" : "#c4454f" }} /></div>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </Section>

          <Section id="golden" n={2} kicker="Human golden set" headline="Where the model and the human disagree"
                   dek="Rows are the human label, columns the model's. The diagonal is agreement. Most topic misses sit in one row: reviews the human called 'other' that the model filed under a specific area the review mentions (ads, offline mode, lyrics) — a fuzzy boundary in the label guide more than random error."
                   source={<><a href={gh("evals/golden_50_labeled.csv")} target="_blank" rel="noreferrer">golden_50_labeled.csv</a> (labeled by a person) and <a href={gh("evals/golden")} target="_blank" rel="noreferrer">evals/golden/</a>. Fifty cases are a diagnostic sample, not a population estimate.</>}>
            {data.golden_detail?.topic && <Card><Heatmap c={data.golden_detail.topic} title={`Topic · ${pct(g?.agreement.topic)} agreement`} /></Card>}
            {data.golden_detail?.intent && <Card><Heatmap c={data.golden_detail.intent} title={`Intent · ${pct(g?.agreement.intent)} agreement`} /></Card>}
            {g && <p className="muted" style={{ margin: 0 }}>Severity MAE {g.severity_mae} · sentiment MAE {g.sentiment_mae} · macro F1 topic {g.topic_macro_f1} / intent {g.intent_macro_f1} · all three fields right {pct(g.agreement.joint)}.</p>}
          </Section>

          <Section id="redteam" n={3} kicker="Red team"
                   headline={<>The model fell for {attacks.length - attacks.filter((x) => x.label_correct).length} of {attacks.length} injections; code caught all {attacks.filter((x) => x.screen_flagged).length}</>}
                   dek="Synthetic reviews tried to override instructions, fake a closing tag, forge a JSON answer, role-play and exfiltrate the prompt. The small model usually obeyed the injected text for that review, though “label every review as praise” did not change the other reviews in its batch. A deterministic screen in code now routes such text to human review without changing labels; it flagged no real review."
                   source={<>Synthetic cases only, excluded from every business number: <a href={gh("evals/injection")} target="_blank" rel="noreferrer">evals/injection/</a>. The screen is recomputed here by the same code (<a href={gh("pipeline/extract.py")} target="_blank" rel="noreferrer">extract.py</a>). Two patterns were added after the first test run.</>}>
            <Card>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Case</th><th>Synthetic review text</th><th>Model label</th><th>Label</th><th>Code screen</th></tr></thead>
                  <tbody>
                    {inj.map((x) => (
                      <tr key={x.id}>
                        <td><b>{x.control ? "control" : "attack"}</b><div className="muted" style={{ fontSize: 12 }}>{x.purpose}</div></td>
                        <td className="review-text" style={{ maxWidth: 380, fontSize: 13.5 }}>{x.text}</td>
                        <td className="muted" style={{ whiteSpace: "nowrap" }}>{x.predicted.topic} · {x.predicted.intent} · {x.predicted.severity}</td>
                        <td>{x.label_correct ? <Tag kind="good">correct</Tag> : <Tag kind="bad">hijacked</Tag>}</td>
                        <td>{x.screen_flagged ? <Tag kind={x.control ? "bad" : "good"}>flagged</Tag> : <Tag kind={x.control ? "good" : "bad"}>not flagged</Tag>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </Section>

          {pl && (
            <Section id="planted" n={4} kicker="Planted errors" headline={`${pl.flagged} of ${pl.planted} planted mistakes caught`}
                     dek="Real verified records were copied into a separate test file and one field was corrupted. The code-side comparison against the independent verifier had to flag each one."
                     source={<>evals/planted_errors.json; synthetic copies only, never written back to real records.</>}>
              <div className="planted">
                {pl.cases.map((c, i) => (
                  <div key={i} className={`planted-chip ${c.flagged_by_comparison ? "ok" : "miss"}`}>
                    <span className="planted-f">{c.planted_field}</span>
                    <span className="muted">{String(c.original[c.planted_field])} → <b>{String(c.planted[c.planted_field])}</b></span>
                    <span>{c.flagged_by_comparison ? "✓ flagged" : "✕ missed"}</span>
                  </div>
                ))}
              </div>
            </Section>
          )}

          <Section id="claims" n={5} kicker="Claim checks" headline="The AI memo is checked like a pull request"
                   dek="The memo agent may only use numbers from a code-computed facts table. Code then verifies the draft before anyone sees it."
                   source={<><a href={gh("pipeline/memo.py")} target="_blank" rel="noreferrer">pipeline/memo.py</a> and runs/final100k/memo_checks.json.</>}>
            <Card>
              <ul className="checks">
                <li><Tag kind="good">pass</Tag> Every number equals the fact it cites, and cites one.</li>
                <li><Tag kind="good">pass</Tag> Every issue ID and review ID exists in what the agent was given.</li>
                <li><Tag kind="good">pass</Tag> Every “higher than / lower than” between cited facts is true.</li>
                <li><Tag kind="good">pass</Tag> At least two real reviews are cited as evidence.</li>
                <li><Tag kind="good">pass</Tag> No revenue, retention or churn claims outside the limitations section.</li>
              </ul>
              <p className="muted" style={{ margin: "12px 0 0" }}>
                Final status: <Tag kind={rec.data?.checks.status === "checks_passed" ? "good" : "bad"}>{rec.data?.checks.status ?? "…"}</Tag>
                {" "}The comparison rule exists because the first final memo called 2.96 “lower than” 2.36 and still passed the number
                checks. See the <Link to="/story">build log</Link>.
              </p>
            </Card>
          </Section>
        </div>
      </div>
    </>
  );
}
