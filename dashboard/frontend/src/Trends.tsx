import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, fmt, Status, useApi } from "./components";
import { AREAS, chart } from "./theme";

type Row = { month: string; area: string; reviews: number; complaints: number };

const FOCUS = AREAS.filter((a) => a.focus);

export default function Trends() {
  const { data, error, loading } = useApi<Row[]>("/api/trends");
  if (!data) return <Status loading={loading} error={error} />;
  const months = [...new Set(data.map((r) => r.month))].sort();
  const rows = months.map((m) => {
    const out: Record<string, number | string> = { month: m };
    for (const { key } of FOCUS) {
      const r = data.find((x) => x.month === m && x.area === key);
      out[key] = r && r.reviews ? Math.round((r.complaints / r.reviews) * 1000) / 10 : 0;
    }
    out.reviews = data.find((x) => x.month === m && x.area === "all")?.reviews ?? 0;
    return out;
  });
  return (
    <Card title="Complaints per 100 reviews, by month">
      <ResponsiveContainer width="100%" height={290}>
        <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid {...chart.grid} />
          <XAxis dataKey="month" {...chart.axis} tickLine={false} minTickGap={18} />
          <YAxis {...chart.axis} width={44} unit="%" tickLine={false} axisLine={false} />
          <Tooltip
            {...chart.tooltip}
            cursor={{ stroke: "#c7bda9", strokeWidth: 1 }}
            formatter={(v: number, name: string) => [`${v}%`, name]}
            labelFormatter={(m: string) => `${m} · ${fmt(Number(rows.find((r) => r.month === m)?.reviews))} reviews (denominator)`}
          />
          <Legend iconType="plainline" wrapperStyle={{ paddingTop: 8 }} />
          {FOCUS.map(({ key, label, color }) => (
            <Line key={key} dataKey={key} name={label} stroke={color} strokeWidth={2} dot={false}
                  activeDot={{ r: 4, stroke: "#fffefb", strokeWidth: 2 }} />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <p className="muted" style={{ fontSize: 12.5, marginBottom: 0 }}>
        Share = complaint + cancellation records in the area ÷ all reviews in scope that month; hover a month for its denominator.
        {" "}{months[0]} and {months[months.length - 1]} are partial months.
      </p>
    </Card>
  );
}
