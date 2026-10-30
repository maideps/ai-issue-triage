import { useState, type FormEvent } from "react";
import { api, type Run } from "./api";
import { KpiBar } from "./components/KpiBar";
import { QueueList, runKey } from "./components/QueueList";
import { RunDetail } from "./components/RunDetail";

export function App() {
  const [selected, setSelected] = useState<Pick<Run, "repository" | "number"> | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [replayNumber, setReplayNumber] = useState("");
  const [replayState, setReplayState] = useState<string | null>(null);
  const repository = "astral-sh/uv";

  async function replay(event: FormEvent) {
    event.preventDefault();
    const number = Number(replayNumber);
    if (!Number.isInteger(number) || number <= 0) return;
    setReplayState(`Replaying #${number}…`);
    try {
      await api.enqueue(repository, number);
      setReplayState(null);
      setReplayNumber("");
      setSelected({ repository, number });
      setRefreshKey((k) => k + 1);
    } catch (e) {
      setReplayState(`Could not replay #${number}: ${(e as Error).message}`);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Issue triage review</h1>
        <p className="muted">
          Every suggestion waits for a maintainer. Nothing is posted to real repositories.
        </p>
        <form className="replay" onSubmit={replay}>
          <label htmlFor="replay-number">Replay a {repository} issue as of its creation</label>
          <input
            id="replay-number"
            inputMode="numeric"
            placeholder="issue number"
            value={replayNumber}
            onChange={(e) => setReplayNumber(e.target.value.trim())}
          />
          <button type="submit" disabled={!replayNumber}>
            Replay
          </button>
          {replayState && <span role="status">{replayState}</span>}
        </form>
      </header>
      <KpiBar refreshKey={refreshKey} />
      <main>
        <QueueList
          selected={selected ? runKey(selected) : null}
          onSelect={(run) => setSelected({ repository: run.repository, number: run.number })}
          refreshKey={refreshKey}
        />
        {selected ? (
          <RunDetail
            key={runKey(selected)}
            repository={selected.repository}
            number={selected.number}
            onReviewed={() => setRefreshKey((k) => k + 1)}
          />
        ) : (
          <p className="muted placeholder">Select a suggestion to review it.</p>
        )}
      </main>
    </div>
  );
}
