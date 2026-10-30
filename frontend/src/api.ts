// Typed client for the review API (backend/src/issue_triage/api/review.py).

export type RunState = "pending" | "approved" | "rejected";
export type Decision = "approve" | "edit" | "reject";

export interface SimilarIssue {
  number: number;
  similarity: number;
  title: string;
}

export interface Evidence {
  decision: { action: string; reason: string } | null;
  triage: { label: string; confidence: number; rationale: string } | null;
  guard: { flagged: boolean; signals: string[]; redactions: Record<string, number> } | null;
  similar: SimilarIssue[];
}

export interface Run {
  repository: string;
  number: number;
  title: string;
  status: RunState;
  action: string;
  confidence: number;
  needs_attention: boolean;
  proposal_text: string;
  citations: string[];
  evidence: Evidence;
  final_text: string | null;
  updated_at: string | null;
}

export interface Feedback {
  decision: Decision;
  original_text: string;
  final_text: string | null;
  reviewer: string;
  created_at: string | null;
}

export interface MaintainerOutcome {
  state: string;
  state_reason: string | null;
  closed_at: string | null;
  labels: string[];
  type_label: string | null;
  duplicate_of: number | null;
  needs_info: boolean;
  first_reply: string | null;
}

export interface Agreement {
  expected_action: string | null;
  action_matches: boolean | null;
  duplicate_target_matches: boolean | null;
  type_matches: boolean | null;
}

export interface RunDetail {
  run: Run;
  issue: { title: string; body: string; created_at: string; labels: string[] };
  feedback: Feedback[];
  maintainer: MaintainerOutcome;
  agreement: Agreement;
}

export interface Kpis {
  live: {
    runs: number;
    pending: number;
    approved: number;
    rejected: number;
    approval_rate: number | null;
    edit_rate: number | null;
    escalation_rate: number | null;
    traced_runs: number;
    llm_calls: number;
    cost_per_issue_usd: number | null;
    latency_ms_p50: number | null;
    latency_ms_p95: number | null;
    errors: number;
  };
  offline: {
    triage_macro_f1: number | null;
    triage_escalation_rate: number | null;
    duplicate_recall_at_10: number | null;
    answer_faithfulness: number | null;
    answer_citation_validity: number | null;
    replay_action_agreement: number | null;
  };
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? detail;
    } catch {
      // keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

const runPath = (repository: string, number: number) => `/api/runs/${repository}/${number}`;

export const api = {
  listRuns: (status?: RunState) =>
    request<Run[]>(`/api/runs${status ? `?status=${status}` : ""}`),
  kpis: () => request<Kpis>("/api/kpis"),
  getRun: (repository: string, number: number) =>
    request<RunDetail>(runPath(repository, number)),
  enqueue: (repository: string, number: number) =>
    request<RunDetail>(runPath(repository, number), { method: "POST" }),
  review: (repository: string, number: number, decision: Decision, text?: string, reviewer = "maintainer") =>
    request<RunDetail>(`${runPath(repository, number)}/review`, {
      method: "POST",
      body: JSON.stringify({ decision, text, reviewer }),
    }),
};
