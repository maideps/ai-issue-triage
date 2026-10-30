import { useEffect, useState } from "react";
import { api, type Kpis } from "../api";

const pct = (value: number | null) => (value === null ? "—" : `${Math.round(value * 100)}%`);
const num = (value: number | null, digits = 2) => (value === null ? "—" : value.toFixed(digits));
const ms = (value: number | null) => (value === null ? "—" : `${Math.round(value)} ms`);
const usd = (value: number | null) => (value === null ? "—" : `$${value.toFixed(4)}`);

export function KpiBar({ refreshKey }: { refreshKey: number }) {
  const [kpis, setKpis] = useState<Kpis | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .kpis()
      // An unexpected payload hides the strip instead of breaking the queue below it.
      .then((result) => !cancelled && setKpis(result && "live" in result ? result : null))
      .catch(() => !cancelled && setKpis(null));
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);

  if (!kpis) return null;
  const { live, offline } = kpis;
  const items: [string, string, string][] = [
    ["Pending", String(live.pending), "suggestions waiting for review"],
    ["Approved", pct(live.approval_rate), "of reviewed suggestions"],
    ["Edited", pct(live.edit_rate), "of approvals needed an edit"],
    ["Escalated", pct(live.escalation_rate), "triage calls sent to the LLM"],
    ["Cost / issue", usd(live.cost_per_issue_usd), "model spend per run"],
    ["Latency p50 / p95", `${ms(live.latency_ms_p50)} / ${ms(live.latency_ms_p95)}`, "agent time, excluding the human gate"],
    ["Triage F1", num(offline.triage_macro_f1), "macro-F1 on the test split"],
    ["Duplicate recall@10", num(offline.duplicate_recall_at_10), "319 maintainer-linked pairs"],
    ["Replay agreement", pct(offline.replay_action_agreement), "same action as maintainers"],
  ];
  return (
    <section className="kpis" aria-label="Key metrics">
      {items.map(([label, value, hint]) => (
        <div key={label} className="kpi" title={hint}>
          <span className="kpi-value">{value}</span>
          <span className="kpi-label">{label}</span>
        </div>
      ))}
    </section>
  );
}
