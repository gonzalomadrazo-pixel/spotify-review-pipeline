import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, fmt, Status, useApi } from "./components";

type Row = { month: string; area: string; reviews: number; complaints: number };

const AREAS: [string, string, string][] = [
  ["access", "access", "#2f6fb0"],
  ["usability", "usability", "#2f8f62"],
  ["playback", "playback", "#c95d3a"],
  ["billing_support", "billing / support", "#8a5bb5"],
];

export default function Trends() {
  const { data, error, loading } = useApi<Row[]>("/api/trends");
  if (!data) return <Status loading={loading} error={error} />;
  const months = [...new Set(data.map((r) => r.month))].sort();
  const rows = months.map((m) => {
    const out: Record<string, number | string> = { month: m };
    for (const [key] of AREAS) {
      const r = data.find((x) => x.month === m && x.area === key);
      out[key] = r && r.reviews ? Math.round((r.complaints / r.reviews) * 1000) / 10 : 0;
    }
    out.reviews = data.find((x) => x.month === m && x.area === "all")?.reviews ?? 0;
    return out;
  });
  return (
    <Card title="Complaints per 100 reviews, by month and product area">
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
          <XAxis dataKey="month" stroke="var(--muted)" tick={{ fontSize: 11 }} />
          <YAxis stroke="var(--muted)" width={40} unit="%" />
          <Tooltip
            contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)" }}
            formatter={(v: number, name: string) => [`${v}%`, name]}
            labelFormatter={(m: string) => `${m} · ${fmt(Number(rows.find((r) => r.month === m)?.reviews))} reviews (denominator)`}
          />
          <Legend />
          {AREAS.map(([key, label, color]) => (
            <Line key={key} dataKey={key} name={label} stroke={color} strokeWidth={2} dot={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <p className="muted">
        Share = complaint + cancellation records in the area ÷ all reviews in scope that month. Hover a month to see its denominator.
        The first ({months[0]}) and last ({months[months.length - 1]}) months are partial. Comparable-period figures (June–October
        2022 vs 2023) are in the memo's facts table.
      </p>
    </Card>
  );
}
