import { useEffect, useState } from "react";
import { api, type Run, type RunState } from "../api";

const TABS: { label: string; status: RunState }[] = [
  { label: "Pending", status: "pending" },
  { label: "Approved", status: "approved" },
  { label: "Rejected", status: "rejected" },
];

interface Props {
  selected: string | null;
  onSelect: (run: Run) => void;
  refreshKey: number;
}

export const runKey = (run: Pick<Run, "repository" | "number">) => `${run.repository}#${run.number}`;

export function QueueList({ selected, onSelect, refreshKey }: Props) {
  const [status, setStatus] = useState<RunState>("pending");
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setRuns(null);
    setError(null);
    api
      .listRuns(status)
      .then((result) => !cancelled && setRuns(result))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [status, refreshKey]);

  return (
    <nav className="queue" aria-label="Review queue">
      <div className="tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.status}
            role="tab"
            aria-selected={status === tab.status}
            onClick={() => setStatus(tab.status)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {error && <p role="alert">Could not load the queue: {error}</p>}
      {!error && runs === null && <p className="muted">Loading…</p>}
      {runs?.length === 0 && <p className="muted">Nothing {status} right now.</p>}
      <ul>
        {runs?.map((run) => (
          <li key={runKey(run)}>
            <button
              className={selected === runKey(run) ? "item selected" : "item"}
              onClick={() => onSelect(run)}
            >
              <span className="item-title">
                {run.needs_attention && <span className="badge attention">attention</span>}#
                {run.number} {run.title}
              </span>
              <span className="item-meta">
                <span className={`badge action-${run.action}`}>{run.action}</span>
                {Math.round(run.confidence * 100)}%
              </span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
