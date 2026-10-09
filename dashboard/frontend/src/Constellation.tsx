import { KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Area, Issue } from "./api";
import { fmt } from "./components";
import { AREA_BY_KEY, AREAS, AreaKey, areaOf, NEUTRAL, SEV_COLORS } from "./theme";

/** Issue network: the corpus core, one hub per product area, one node per issue.
 * Encoding: color = area (validated palette, hubs direct-labeled), star size = complaint count (area ∝ count),
 * halo = mean severity ≥ 3. The Issue ranking page is the table view of the same data. */

const ORDER: AreaKey[] = ["other", "playback", "billing_support", "downloads", "catalog", "access", "usability"];

type Star = { issue: Issue; area: AreaKey; x: number; y: number; r: number; ux: number; uy: number };
type Hub = { key: AreaKey; x: number; y: number; r: number; ux: number; uy: number; complaints: number; area?: Area; stars: Star[] };
type Hover = { kind: "issue"; star: Star } | { kind: "area"; hub: Hub } | null;

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(960);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return { ref, width };
}

export default function Constellation({ issues, areas, totalComplaints }: { issues: Issue[]; areas: Area[]; totalComplaints: number }) {
  const { ref, width } = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<Hover>(null);
  const nav = useNavigate();
  const portrait = width < 640;
  const w = Math.max(300, width);
  const h = portrait ? Math.round(w * 1.4) : Math.round(Math.min(640, Math.max(460, w * 0.6)));

  const layout = useMemo(() => {
    const cx = w / 2, cy = h / 2;
    const rx = w * (portrait ? 0.29 : 0.335), ry = h * (portrait ? 0.355 : 0.33);
    const s = Math.max(0.62, Math.min(1.15, Math.min(w, h * 1.5) / 1000));
    const areaByKey = Object.fromEntries(areas.map((a) => [a.area, a])) as Record<string, Area>;
    const maxArea = Math.max(1, ...areas.map((a) => a.complaint_count));
    const maxIssue = Math.max(1, ...issues.map((i) => i.complaint_count));
    const hubs: Hub[] = ORDER.map((key, i) => {
      const ang = (-90 + (i * 360) / ORDER.length) * (Math.PI / 180);
      const ux = Math.cos(ang), uy = Math.sin(ang);
      const area = areaByKey[key];
      const members = issues.filter((it) => areaOf(it.topic) === key).sort((a, b) => b.complaint_count - a.complaint_count);
      const complaints = area?.complaint_count ?? members.reduce((t, m) => t + m.complaint_count, 0);
      const hub: Hub = { key, x: cx + rx * ux, y: cy + ry * uy, r: (7 + 13 * Math.sqrt(complaints / maxArea)) * s, ux, uy, complaints, area, stars: [] };
      const n = members.length;
      const step = Math.min(32, 175 / Math.max(1, n - 1)) * (Math.PI / 180);
      members.forEach((issue, j) => {
        const a = ang + (j - (n - 1) / 2) * step;
        const dist = (hub.r / s + 46 + (j % 2) * 30) * s;
        const sx = Math.cos(a), sy = Math.sin(a);
        hub.stars.push({ issue, area: key, x: hub.x + dist * sx, y: hub.y + dist * sy, r: (2.6 + 10 * Math.sqrt(issue.complaint_count / maxIssue)) * s, ux: sx, uy: sy });
      });
      return hub;
    });
    return { cx, cy, s, hubs, maxArea };
  }, [issues, areas, w, h, portrait]);

  const focus: AreaKey | null = hover ? (hover.kind === "issue" ? hover.star.area : hover.hub.key) : null;
  const dim = (k: AreaKey) => (focus && focus !== k ? 0.16 : 1);
  const open = (st: Star) => nav(`/issues/${encodeURIComponent(st.issue.issue_id)}`);
  const onKey = (st: Star) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(st); }
  };
  const { cx, cy, s, hubs } = layout;
  const tip = hover && (hover.kind === "issue" ? hover.star : hover.hub);

  return (
    <div className="constellation" ref={ref}>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} role="img"
           aria-label={`Network of ${issues.length} issues in ${hubs.length} product areas; the Issue ranking page lists the same data as a table.`}
           onMouseLeave={() => setHover(null)}>
        <defs>
          {AREAS.filter((a, i, all) => all.findIndex((b) => b.color === a.color) === i).map((a) => (
            <radialGradient key={a.color} id={`glow-${a.color.slice(1)}`}>
              <stop offset="0%" stopColor={a.color} stopOpacity="0.5" />
              <stop offset="60%" stopColor={a.color} stopOpacity="0.1" />
              <stop offset="100%" stopColor={a.color} stopOpacity="0" />
            </radialGradient>
          ))}
          <radialGradient id="core-glow">
            <stop offset="0%" stopColor="#336d5d" stopOpacity="0.22" />
            <stop offset="100%" stopColor="#336d5d" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* guide rings */}
        {[0.5, 1].map((k) => (
          <ellipse key={k} className="ring" cx={cx} cy={cy} rx={w * (portrait ? 0.29 : 0.335) * k} ry={h * (portrait ? 0.355 : 0.33) * k} />
        ))}

        {/* spokes: core -> hub, width by share of complaints, with a travelling signal */}
        {hubs.map((hb, i) => {
          const color = AREA_BY_KEY[hb.key].color;
          const width = 0.8 + 3.2 * (hb.complaints / Math.max(1, totalComplaints));
          const d = `M${cx},${cy} L${hb.x},${hb.y}`;
          return (
            <g key={hb.key} style={{ opacity: dim(hb.key) }} className="fade">
              <path d={d} stroke={color} strokeOpacity={0.3} strokeWidth={width * 1.6} fill="none" />
              <path d={d} className="signal" stroke={color} strokeWidth={width + 0.6} fill="none" style={{ animationDelay: `${i * -0.37}s` }} />
              {hb.stars.map((st, j) => (
                <g key={st.issue.issue_id}>
                  <line x1={hb.x} y1={hb.y} x2={st.x} y2={st.y} stroke={color} strokeOpacity={0.45} strokeWidth={0.9} />
                  {j > 0 && (
                    <line x1={hb.stars[j - 1].x} y1={hb.stars[j - 1].y} x2={st.x} y2={st.y} stroke={color} strokeOpacity={0.25} strokeWidth={0.7} />
                  )}
                </g>
              ))}
            </g>
          );
        })}

        {/* core */}
        <g className="core">
          <circle cx={cx} cy={cy} r={58 * s} fill="url(#core-glow)" />
          <circle cx={cx} cy={cy} r={22 * s} className="core-ring" />
          <circle cx={cx} cy={cy} r={11 * s} fill="#14130f" />
          <text x={cx} y={cy + 30 * s + 22} className="core-value" textAnchor="middle">{fmt(totalComplaints)}</text>
          <text x={cx} y={cy + 30 * s + 43} className="core-label" textAnchor="middle">COMPLAINTS</text>
        </g>

        {/* hubs */}
        {hubs.map((hb) => {
          const a = AREA_BY_KEY[hb.key];
          // Wide: label sits between hub and core. Narrow: centered on the hub, nudged vertically toward the core,
          // so labels of side-by-side hubs cannot run into each other.
          const lx = portrait ? hb.x : hb.x - hb.ux * (hb.r + 14);
          const ly = portrait ? (hb.uy < -0.2 ? hb.y + hb.r + 16 : hb.y - hb.r - 9) : hb.y - hb.uy * (hb.r + 14);
          const anchor = portrait ? "middle" : hb.ux > 0.3 ? "end" : hb.ux < -0.3 ? "start" : "middle";
          const dy = portrait ? 0 : hb.uy > 0.3 ? -14 : 4;
          return (
            <g key={hb.key} className="hub fade" style={{ opacity: dim(hb.key) }}
               onMouseEnter={() => setHover({ kind: "area", hub: hb })}>
              <circle cx={hb.x} cy={hb.y} r={hb.r * 2.2} fill={`url(#glow-${a.color.slice(1)})`} />
              <circle cx={hb.x} cy={hb.y} r={hb.r} fill="#fffefb" stroke={a.color} strokeWidth={2.2} />
              <circle cx={hb.x} cy={hb.y} r={hb.r * 0.45} fill={a.color} />
              <circle cx={hb.x} cy={hb.y} r={Math.max(22, hb.r + 6)} fill="transparent" />
              <text x={lx} y={ly + dy} textAnchor={anchor} className="hub-label">{a.label.charAt(0).toUpperCase() + a.label.slice(1)}</text>
              {!portrait && (
                <text x={lx} y={ly + dy + 15} textAnchor={anchor} className="hub-value">{fmt(hb.complaints)}{hb.area ? ` · ${hb.area.share_of_complaints}` : ""}</text>
              )}
            </g>
          );
        })}

        {/* stars (issues) */}
        {hubs.flatMap((hb) => hb.stars).map((st, k) => {
          const a = AREA_BY_KEY[st.area];
          const severe = Number(st.issue.mean_severity) >= 3;
          const active = hover?.kind === "issue" && hover.star.issue.issue_id === st.issue.issue_id;
          return (
            <g key={st.issue.issue_id} className={`star fade${active ? " active" : ""}`} style={{ opacity: dim(st.area), ["--d" as string]: `${k * 28}ms` }}
               tabIndex={0} role="link"
               aria-label={`${st.issue.title}: ${fmt(st.issue.complaint_count)} complaints, rank ${st.issue.rank}, mean severity ${Number(st.issue.mean_severity).toFixed(2)}`}
               onMouseEnter={() => setHover({ kind: "issue", star: st })} onFocus={() => setHover({ kind: "issue", star: st })}
               onBlur={() => setHover(null)} onClick={() => open(st)} onKeyDown={onKey(st)}>
              <circle cx={st.x} cy={st.y} r={st.r * 2.6} fill={`url(#glow-${a.color.slice(1)})`} className="star-glow" />
              {severe && <circle cx={st.x} cy={st.y} r={st.r + 4} fill="none" stroke={SEV_COLORS[4]} strokeWidth={1.3} className="star-halo" />}
              <circle cx={st.x} cy={st.y} r={st.r} fill={a.color} stroke="#fffefb" strokeWidth={2} className="star-body" />
              {st.issue.rank <= 5 && (
                <text x={st.x + st.ux * (st.r + 9)} y={st.y + st.uy * (st.r + 9) + 4} textAnchor={st.ux > 0.2 ? "start" : st.ux < -0.2 ? "end" : "middle"}
                      className="rank-label">#{st.issue.rank}</text>
              )}
              <circle cx={st.x} cy={st.y} r={Math.max(13, st.r + 7)} fill="transparent" />
            </g>
          );
        })}
      </svg>

      {tip && (
        <div className="cn-tip" style={{ left: Math.min(Math.max(tip.x, 120), w - 120), top: tip.y }} role="status">
          {hover!.kind === "issue" ? (
            <>
              <div className="tip-value">{fmt(hover!.star.issue.complaint_count)} <span>complaints</span></div>
              <div className="tip-title">{hover!.star.issue.title}</div>
              <div className="tip-meta"><i style={{ background: AREA_BY_KEY[hover!.star.area].color }} />{hover!.star.issue.issue_id}</div>
              <div className="tip-meta">rank #{hover!.star.issue.rank} · mean severity {Number(hover!.star.issue.mean_severity).toFixed(2)} · severity sum {fmt(hover!.star.issue.severity_sum)}</div>
              <div className="tip-hint">click to open evidence →</div>
            </>
          ) : (
            <>
              <div className="tip-value">{fmt(hover!.hub.complaints)} <span>complaints</span></div>
              <div className="tip-title">{AREA_BY_KEY[hover!.hub.key].label}</div>
              {hover!.hub.area && (
                <div className="tip-meta">{hover!.hub.area.share_of_complaints} of all complaints · mean severity {Number(hover!.hub.area.mean_severity).toFixed(2)} · {fmt(hover!.hub.area.severe_count)} at severity ≥ 4</div>
              )}
              <div className="tip-meta">{hover!.hub.stars.length} issues in this cluster</div>
            </>
          )}
        </div>
      )}

      <div className="cn-legend">
        {AREAS.filter((a) => a.focus).map((a) => (
          <span key={a.key}><i style={{ background: a.color }} />{a.label}</span>
        ))}
        <span><i style={{ background: NEUTRAL }} />downloads · catalog · other</span>
        <span className="key-size"><b /><b /><b />node size = complaints</span>
        <span><em style={{ borderColor: SEV_COLORS[4] }} />mean severity ≥ 3</span>
        <Link to="/issues">table view →</Link>
      </div>
    </div>
  );
}
