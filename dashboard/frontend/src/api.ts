export type Review = {
  review_id: string;
  idx: number;
  review_text: string;
  review_rating: string;
  review_likes: string;
  app_version: string;
  review_timestamp: string;
  month: string;
  status: string;
  reason: string | null;
  topic: string | null;
  subtopic: string | null;
  intent: string | null;
  sentiment: number | null;
  severity: number | null;
  evidence_quote: string | null;
  needs_review: boolean | null;
  review_reason: string | null;
  entities: string[];
  label_config: string | null;
  cache_source_id: string | null;
  result_source: string | null;
  source_request_id: string | null;
  issue_id: string | null;
};

export type Issue = {
  issue_id: string;
  rank: number;
  topic: string;
  title: string;
  summary: string | null;
  coherence: string | null;
  complaint_count: number;
  severity_sum: number;
  mean_severity: string;
  priority_score: number;
  cancellation_count: number;
  severe_count: number;
  needs_review_count: number;
};

export type Area = {
  area: string;
  complaint_count: number;
  severity_sum: number;
  mean_severity: string;
  share_of_complaints: string;
  severe_count: number;
  cancellation_count: number;
  total_complaints: number;
};

export type Fact = { fact_id: string; scope: string; subject: string; metric: string; value: string; display: string; formula: string };
export type Claim = { claim_id: string; issue_id: string; metric: string; value: string };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}
