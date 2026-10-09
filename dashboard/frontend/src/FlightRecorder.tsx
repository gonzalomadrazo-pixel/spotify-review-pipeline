import { PointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { fmt } from "./components";
import { ACCENT, INK, NEUTRAL } from "./theme";

/** Flight recorder for one pipeline run: every model call on a wall-clock axis, the cumulative texts labeled,
 * shaded pauses (no enrichment call for over 5 minutes) and numbered run events explained in the caption below. */

export type Telemetry = {
  roles: string[];
  calls: [number, number, number, number, number, number, number, number][]; // start, dur, items, role, inv, ok, out, in
  invocations: { n: number; phase: string; stop_reason: string; completed_before: number; completed_after: number; start: number; end: number }[];
  progress: Record<string, number>;
  invalid_outputs: number;
  result_sources: Record<string, number>;
};

const PAUSE_S = 5 * 60;
const ROLE_COLOR = [NEUTRAL, ACCENT, ACCENT, ACCENT];

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(900);
  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    if (ref.current) ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return { ref, w };
}

const when = (t: number) => new Date(t * 1000).toLocaleString("en-US", { weekday: "short", hour: "numeric", minute: "2-digit" });

export default function FlightRecorder({ t }: { t: Telemetry }) {
  const { ref, w } = useWidth();
  const [hover, setHover] = useState<number | null>(null);
  const H = 360, top = 40, curveH = 120, dotTop = 200, dotH = 110, padL = 46, padR = 14;

  const m = useMemo(() => {
    const calls = t.calls.filter((c) => c[0]);
    const t0 = calls[0]?.[0] ?? 0;
    const t1 = calls.length ? Math.max(...calls.map((c) => c[0] + c[1])) : 1;
    const X = (tt: number) => padL + ((tt - t0) / Math.max(1, t1 - t0)) * (w - padL - padR);
    let cum = 0;
    const curve = calls.map((c) => { if (c[3] === 0 && c[5]) cum += c[2]; return [X(c[0] + c[1]), cum] as [number, number]; });
    const pauses: [number, number][] = [];
    for (let i = 1; i < calls.length; i++) {
      const end = calls[i - 1][0] + calls[i - 1][1];
      // only gaps inside the labeling run count as pauses (later gaps are deliberate re-runs, not sleep)
      if (calls[i][0] - end > PAUSE_S && calls[i][3] === 0 && calls[i - 1][3] === 0) pauses.push([end, calls[i][0]]);
    }
    const ticks: number[] = [];
    const startHour = Math.ceil(t0 / 21600) * 21600; // every 6 hours
    for (let tt = startHour; tt < t1; tt += 21600) ticks.push(tt);
    const maxDur = Math.max(60, ...calls.map((c) => c[1]));
    return { calls, X, curve, cum, pauses, ticks, maxDur, t0, t1 };
  }, [t, w]);

  const inv = t.invocations.filter((i) => i.start && i.end && i.completed_after > i.completed_before);
  const lastInv = inv[inv.length - 1];
  const memoRuns = t.invocations.filter((i) => i.start && i.completed_after === i.completed_before);
  const events: { n: string; t: number; text: string }[] = [
    ...inv.map((i, k) => ({ n: String(k + 1), t: i.start,
      text: `Invocation ${i.n} (${i.phase}) starts at ${fmt(i.completed_before)} classified` +
        (i.stop_reason === "stop_file" ? `; stopped with a STOP file at ${fmt(i.completed_after)} and resumed from the checkpoint` : `; finishes at ${fmt(i.completed_after)}`) })),
    ...(lastInv ? [{ n: "✓", t: lastInv.end, text: `All ${fmt(lastInv.completed_after)} classified; verification, grouping and the memo follow` }] : []),
    ...(memoRuns.length ? [{ n: "M", t: memoRuns[0].start, text: `${memoRuns.length} memo-only re-runs after the claim checker was strengthened (no reviews re-labeled)` }] : []),
  ];
  const yCurve = (v: number) => top + curveH - (v / Math.max(1, m.cum)) * curveH;
  const yDot = (d: number) => dotTop + dotH - (Math.min(d, m.maxDur) / m.maxDur) * dotH;
  const path = m.curve.map(([x, v], i) => `${i ? "L" : "M"}${x.toFixed(1)},${yCurve(v).toFixed(1)}`).join("");
  const pauseTotal = m.pauses.reduce((n, [a, b]) => n + (b - a), 0);
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * w;
    let best = -1, bd = 1e9;
    m.calls.forEach((c, i) => { const d = Math.abs(m.X(c[0]) - px); if (d < bd) { bd = d; best = i; } });
    setHover(bd < 24 ? best : null);
  };
  const hc = hover != null ? m.calls[hover] : null;
  // spread event markers that would overlap
  let lastX = -99;
  const marks = events.sort((a, b) => a.t - b.t).map((e) => { const x = Math.max(m.X(e.t), lastX + 22); lastX = x; return { ...e, x }; });

  return (
    <div className="recorder" ref={ref}>
      <svg viewBox={`0 0 ${w} ${H}`} width="100%" height={H} onPointerMove={onMove} onPointerLeave={() => setHover(null)}
           role="img" aria-label={`Timeline of ${m.calls.length} model calls; the caption below lists the run events`}>
        {m.pauses.map(([a, b], i) => (
          <rect key={i} x={m.X(a)} y={top - 4} width={Math.max(1.5, m.X(b) - m.X(a))} height={dotTop + dotH - top + 4} className="rec-pause" />
        ))}
        {m.pauses.length > 0 && (
          <text x={m.X(m.pauses[0][0])} y={top - 10} className="rec-note">▒ laptop asleep · {(pauseTotal / 3600).toFixed(1)} h of pauses</text>
        )}
        {marks.map((e) => (
          <g key={e.n + e.t}>
            <line x1={m.X(e.t)} x2={m.X(e.t)} y1={top + 2} y2={dotTop + dotH} stroke={INK} strokeOpacity={0.25} />
            <circle cx={e.x} cy={18} r={9} fill={e.n === "✓" ? ACCENT : "#fffefb"} stroke={e.n === "✓" ? ACCENT : INK} strokeWidth={1.2} />
            <text x={e.x} y={22} textAnchor="middle" className="rec-mark" style={{ fill: e.n === "✓" ? "#fff" : INK }}>{e.n}</text>
          </g>
        ))}
        <line x1={padL} x2={w - padR} y1={top + curveH} y2={top + curveH} className="rec-axis" />
        <path d={`${path}L${m.curve.length ? m.curve[m.curve.length - 1][0] : padL},${top + curveH}L${padL},${top + curveH}Z`} fill={ACCENT} fillOpacity={0.08} />
        <path d={path} fill="none" stroke={ACCENT} strokeWidth={2} />
        <text x={padL - 6} y={top + 6} textAnchor="end" className="rec-tick">{fmt(m.cum)}</text>
        <text x={padL - 6} y={top + curveH} textAnchor="end" className="rec-tick">0</text>
        <text x={padL + 8} y={top + 16} className="rec-note">texts labeled by the model, cumulative</text>
        <line x1={padL} x2={w - padR} y1={dotTop + dotH} y2={dotTop + dotH} className="rec-axis" />
        <text x={padL - 6} y={dotTop + 6} textAnchor="end" className="rec-tick">{Math.round(m.maxDur)}s</text>
        <text x={padL - 6} y={dotTop + dotH} textAnchor="end" className="rec-tick">0</text>
        <text x={padL + 8} y={dotTop + 8} className="rec-note">seconds per model call</text>
        {m.calls.map((c, i) => (
          <circle key={i} cx={m.X(c[0])} cy={yDot(c[1])} r={c[3] === 0 ? 1.7 : 3.4} fill={c[5] ? ROLE_COLOR[c[3]] ?? INK : "#b3261e"}
                  fillOpacity={c[3] === 0 ? 0.5 : 0.95} />
        ))}
        {m.ticks.map((tt) => (
          <g key={tt}>
            <line x1={m.X(tt)} x2={m.X(tt)} y1={dotTop + dotH} y2={dotTop + dotH + 5} className="rec-axis" />
            <text x={m.X(tt)} y={dotTop + dotH + 18} textAnchor="middle" className="rec-tick">{when(tt)}</text>
          </g>
        ))}
        {hc && (
          <g pointerEvents="none">
            <line x1={m.X(hc[0])} x2={m.X(hc[0])} y1={top} y2={dotTop + dotH} stroke={INK} strokeOpacity={0.4} />
            <circle cx={m.X(hc[0])} cy={yDot(hc[1])} r={5.5} fill="none" stroke={INK} strokeWidth={1.5} />
          </g>
        )}
      </svg>
      {hc && (
        <div className="rec-tip" style={{ left: Math.min(Math.max(m.X(hc[0]), 110), w - 110) }}>
          <div className="tip-value">{hc[1].toFixed(0)}<span> s</span></div>
          <div className="tip-meta"><b>{t.roles[hc[3]] ?? "call"}</b> · {hc[2] ? `${hc[2]} texts` : "aggregate input"} · invocation {hc[4]}</div>
          <div className="tip-meta">{fmt(hc[7])} tokens in · {fmt(hc[6])} out · {hc[5] ? "succeeded" : "failed"}</div>
          <div className="tip-meta">{when(hc[0])}</div>
        </div>
      )}
      <ol className="rec-events">
        {marks.map((e) => <li key={e.n + e.t}><span className={`rec-badge${e.n === "✓" ? " done" : ""}`}>{e.n}</span><span className="muted">{when(e.t)}</span> {e.text}</li>)}
      </ol>
      <div className="cn-legend">
        <span><i style={{ background: NEUTRAL }} />enrichment call (up to 50 texts)</span>
        <span><i style={{ background: ACCENT }} />verify · group · memo call</span>
        <span><i style={{ background: "rgba(51,109,93,0.25)", borderRadius: 2 }} />cumulative texts labeled</span>
        <span><i style={{ background: "rgba(20,19,15,0.08)", borderRadius: 2 }} />no enrichment call for 5+ min</span>
      </div>
    </div>
  );
}
