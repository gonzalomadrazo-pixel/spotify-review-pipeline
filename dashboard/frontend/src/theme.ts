/** Shared encodings. Colors follow the entity (area) everywhere on the site, never its rank.
 *
 * Area hues were checked with the dataviz palette validator against the paper surface #f6f4ee, all pairs
 * (the network is a scatter-like form): lightness band, chroma floor, CVD ΔE ≥ 8.5, normal-vision ΔE ≥ 18.0,
 * contrast ≥ 3:1. The three minor areas share one warm neutral (3.6:1) and are always direct-labeled.
 * Severity is ordinal (1-5): one hue, light → dark, lightest step 2.08:1 on the surface (validated --ordinal). */

export type AreaKey = "access" | "usability" | "playback" | "billing_support" | "downloads" | "catalog" | "other";

export const NEUTRAL = "#857f73";
export const INK = "#14130f";
export const PAPER = "#f6f4ee";
export const ACCENT = "#336d5d";

export const AREAS: { key: AreaKey; label: string; color: string; focus: boolean }[] = [
  { key: "access", label: "access", color: "#1490b8", focus: true },
  { key: "usability", label: "usability", color: "#7b5ce6", focus: true },
  { key: "playback", label: "playback", color: "#d6408e", focus: true },
  { key: "billing_support", label: "billing / support", color: "#b56d0b", focus: true },
  { key: "downloads", label: "downloads", color: NEUTRAL, focus: false },
  { key: "catalog", label: "catalog", color: NEUTRAL, focus: false },
  { key: "other", label: "other", color: NEUTRAL, focus: false },
];

export const AREA_BY_KEY = Object.fromEntries(AREAS.map((a) => [a.key, a])) as Record<AreaKey, (typeof AREAS)[number]>;

/** Issue topics map onto the brief's product areas (billing and support are combined). */
export function areaOf(topic: string | null | undefined): AreaKey {
  if (topic === "billing" || topic === "support" || topic === "billing_support") return "billing_support";
  return (topic && topic in AREA_BY_KEY ? topic : "other") as AreaKey;
}

export const areaColor = (topic: string | null | undefined) => AREA_BY_KEY[areaOf(topic)].color;
export const areaLabel = (topic: string | null | undefined) => AREA_BY_KEY[areaOf(topic)].label;

export const SEV_COLORS = ["", "#e8968d", "#da6d66", "#c4454f", "#9e313f", "#6e1f2c"];

/** Recharts chrome: solid warm hairline grid, muted axes, paper tooltip. */
export const chart = {
  grid: { stroke: "#e6e1d3", vertical: false },
  axis: { stroke: "#cfc7b4", tick: { fill: "#6b6558", fontSize: 11, fontFamily: "Inter, system-ui, sans-serif" } },
  tooltip: {
    contentStyle: {
      background: "#fffefb",
      border: "1px solid #e2dccb",
      borderRadius: 4,
      boxShadow: "0 14px 34px -14px rgba(60, 48, 24, 0.28)",
      color: INK,
      fontFamily: "Inter, system-ui, sans-serif",
      fontSize: 12,
    },
    labelStyle: { color: "#6b6558", marginBottom: 4 },
    itemStyle: { color: INK },
    cursor: { fill: "rgba(51, 109, 93, 0.06)" },
  },
};
