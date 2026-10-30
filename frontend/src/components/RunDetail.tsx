import { useEffect, useState } from "react";
import { api, type Decision, type RunDetail as Detail } from "../api";

interface Props {
  repository: string;
  number: number;
  onReviewed: () => void;
}

const issueUrl = (repository: string, number: number) =>
  `https://github.com/${repository}/issues/${number}`;

function Verdict({ label, value }: { label: string; value: boolean | null }) {
  if (value === null) return null;
  return (
    <span className={value ? "badge agree" : "badge disagree"}>
      {label}: {value ? "matches" : "differs"}
    </span>
  );
}

export function RunDetail({ repository, number, onReviewed }: Props) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    api
      .getRun(repository, number)
      .then((result) => {
        if (cancelled) return;
        setDetail(result);
        setDraft(result.run.final_text ?? result.run.proposal_text);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [repository, number]);

  if (error) return <p role="alert">Could not load #{number}: {error}</p>;
  if (!detail) return <p className="muted">Loading #{number}…</p>;

  const { run, issue, feedback, maintainer, agreement } = detail;
  const edited = draft.trim() !== run.proposal_text.trim();
  const pending = run.status === "pending";

  async function submit(decision: Decision) {
    setBusy(true);
    setError(null);
    try {
      const result = await api.review(repository, number, decision, decision === "edit" ? draft : undefined);
      setDetail(result);
      onReviewed();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="detail" aria-label={`Run for issue ${number}`}>
      <section className="issue">
        <h2>
          #{run.number} {issue.title}
        </h2>
        <p className="muted">
          Opened {new Date(issue.created_at).toLocaleDateString()} · replayed as of that date ·{" "}
          {issue.labels.length ? `maintainers labelled it: ${issue.labels.join(", ")}` : "no labels"}
        </p>
        <pre className="body">{issue.body || "(empty body)"}</pre>

        <section className="maintainers" aria-label="What maintainers did">
          <h3>What maintainers did</h3>
          <p className="muted">Shown for comparison only; the agent never sees it.</p>
          <ul>
            <li>
              {maintainer.state.toLowerCase()}
              {maintainer.state_reason ? ` (${maintainer.state_reason.toLowerCase()})` : ""}
              {maintainer.closed_at ? ` on ${new Date(maintainer.closed_at).toLocaleDateString()}` : ""}
            </li>
            <li>Type: {maintainer.type_label ?? "no type label"}</li>
            {maintainer.duplicate_of !== null && (
              <li>
                Duplicate of{" "}
                <a href={issueUrl(repository, maintainer.duplicate_of)} target="_blank" rel="noreferrer">
                  #{maintainer.duplicate_of}
                </a>
              </li>
            )}
            {maintainer.needs_info && <li>Asked for a reproducible example</li>}
          </ul>
          {maintainer.first_reply && (
            <>
              <h4>First maintainer reply</h4>
              <pre className="body reply">{maintainer.first_reply}</pre>
            </>
          )}
          <div className="agreement">
            <Verdict label="Action" value={agreement.action_matches} />
            <Verdict label="Duplicate target" value={agreement.duplicate_target_matches} />
            <Verdict label="Type" value={agreement.type_matches} />
          </div>
        </section>
      </section>

      <section className="proposal">
        <h3>
          Proposal: <span className={`badge action-${run.action}`}>{run.action}</span>{" "}
          <span className="muted">confidence {Math.round(run.confidence * 100)}%</span>
        </h3>
        {run.needs_attention && (
          <p className="warning" role="note">
            Needs attention: flagged by the guardrails, ungrounded or unsure.
          </p>
        )}
        <label htmlFor="draft">Reply</label>
        <textarea
          id="draft"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          readOnly={!pending}
          rows={8}
        />
        {pending ? (
          <div className="actions">
            <button disabled={busy || edited} onClick={() => submit("approve")}>
              Approve
            </button>
            <button disabled={busy || !edited || !draft.trim()} onClick={() => submit("edit")}>
              Approve edited
            </button>
            <button className="danger" disabled={busy} onClick={() => submit("reject")}>
              Reject
            </button>
          </div>
        ) : (
          <p className="muted">
            {run.status === "approved" ? "Approved" : "Rejected"}
            {feedback.at(-1) ? ` by ${feedback.at(-1)!.reviewer}` : ""}.
          </p>
        )}

        <h4>Evidence</h4>
        <ul className="evidence">
          {run.evidence.decision && <li>Decision: {run.evidence.decision.reason}</li>}
          {run.evidence.triage && (
            <li>
              Triage: {run.evidence.triage.label} ({Math.round(run.evidence.triage.confidence * 100)}%)
            </li>
          )}
          {run.citations.length > 0 && <li>Cited: {run.citations.join(", ")}</li>}
          {run.evidence.guard?.flagged && (
            <li>Guardrails flagged: {run.evidence.guard.signals.join("; ")}</li>
          )}
        </ul>
        {run.evidence.similar.length > 0 && (
          <>
            <h4>Similar earlier issues</h4>
            <ul className="similar">
              {run.evidence.similar.map((s) => (
                <li key={s.number}>
                  <a href={issueUrl(repository, s.number)} target="_blank" rel="noreferrer">
                    #{s.number}
                  </a>{" "}
                  {s.title} <span className="muted">({s.similarity.toFixed(2)})</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </article>
  );
}
