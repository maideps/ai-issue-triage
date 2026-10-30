import { useState } from "react";
import { type Run } from "./api";
import { QueueList, runKey } from "./components/QueueList";
import { RunDetail } from "./components/RunDetail";

export function App() {
  const [selected, setSelected] = useState<Pick<Run, "repository" | "number"> | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="app">
      <header>
        <h1>Issue triage review</h1>
        <p className="muted">
          Every suggestion waits for a maintainer. Nothing is posted to real repositories.
        </p>
      </header>
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
