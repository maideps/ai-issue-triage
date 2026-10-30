import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Run, RunDetail } from "./api";

const run = (number: number, overrides: Partial<Run> = {}): Run => ({
  repository: "astral-sh/uv",
  number,
  title: `Issue ${number}`,
  status: "pending",
  action: "answer",
  confidence: 0.72,
  needs_attention: false,
  proposal_text: "Set UV_CACHE_DIR [concepts/cache#0]",
  citations: ["concepts/cache#0"],
  evidence: {
    decision: { action: "answer", reason: "question: answer from the docs" },
    triage: { label: "question", confidence: 0.8, rationale: "keywords" },
    guard: { flagged: false, signals: [], redactions: {} },
    similar: [{ number: 12, similarity: 0.61, title: "Cache location" }],
  },
  final_text: null,
  updated_at: null,
  ...overrides,
});

const detail = (r: Run, feedback: RunDetail["feedback"] = []): RunDetail => ({
  run: r,
  issue: { title: r.title, body: "How do I move the cache?", created_at: "2025-09-20T10:00:00Z", labels: ["question"] },
  feedback,
});

type Handler = (url: string, init?: RequestInit) => unknown;

function mockApi(handler: Handler) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const body = handler(url, init);
      if (body instanceof Error) {
        return new Response(JSON.stringify({ detail: body.message }), { status: 500 });
      }
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
  return calls;
}

describe("review queue", () => {
  it("lists pending runs with attention first and shows an empty state per tab", async () => {
    mockApi((url) =>
      url.includes("status=pending") ? [run(3, { needs_attention: true }), run(1)] : [],
    );
    render(<App />);

    const items = await screen.findAllByRole("button", { name: /#\d/ });
    expect(items[0]).toHaveTextContent("attention");
    expect(items[0]).toHaveTextContent("#3 Issue 3");
    expect(items[1]).toHaveTextContent("72%");

    await userEvent.click(screen.getByRole("tab", { name: "Approved" }));
    expect(await screen.findByText("Nothing approved right now.")).toBeInTheDocument();
  });

  it("shows an error when the queue cannot load", async () => {
    mockApi(() => new Error("database down"));
    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent("database down");
  });
});

describe("run detail and review actions", () => {
  async function openRun(handler: Handler) {
    const calls = mockApi(handler);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /#1 Issue 1/ }));
    await screen.findByRole("heading", { name: /#1 Issue 1/ });
    return calls;
  }

  it("shows the draft beside the issue with its evidence", async () => {
    await openRun((url) => (url.endsWith("/api/runs/astral-sh/uv/1") ? detail(run(1)) : [run(1)]));

    const article = screen.getByRole("article");
    expect(within(article).getByText("How do I move the cache?")).toBeInTheDocument();
    expect(within(article).getByLabelText("Reply")).toHaveValue("Set UV_CACHE_DIR [concepts/cache#0]");
    expect(within(article).getByText(/Cited: concepts\/cache#0/)).toBeInTheDocument();
    expect(within(article).getByRole("link", { name: "#12" })).toHaveAttribute(
      "href",
      "https://github.com/astral-sh/uv/issues/12",
    );
    expect(within(article).getByRole("button", { name: "Approve edited" })).toBeDisabled();
  });

  it("approves the proposal as is", async () => {
    const calls = await openRun((url, init) => {
      if (url.endsWith("/review")) {
        return detail(run(1, { status: "approved", final_text: "Set UV_CACHE_DIR [concepts/cache#0]" }), [
          { decision: "approve", original_text: "x", final_text: "x", reviewer: "maintainer", created_at: null },
        ]);
      }
      return url.endsWith("/1") && !init?.method ? detail(run(1)) : [run(1)];
    });

    await userEvent.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(screen.getByText(/Approved by maintainer/)).toBeInTheDocument());
    const review = calls.find((c) => c.url.endsWith("/review"))!;
    expect(JSON.parse(String(review.init?.body))).toMatchObject({ decision: "approve" });
  });

  it("sends the edited reply", async () => {
    const calls = await openRun((url, init) => {
      if (url.endsWith("/review")) return detail(run(1, { status: "approved", final_text: "Use UV_CACHE_DIR." }));
      return url.endsWith("/1") && !init?.method ? detail(run(1)) : [run(1)];
    });

    const reply = screen.getByLabelText("Reply");
    await userEvent.clear(reply);
    await userEvent.type(reply, "Use UV_CACHE_DIR.");
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Approve edited" }));

    const review = calls.find((c) => c.url.endsWith("/review"))!;
    expect(JSON.parse(String(review.init?.body))).toMatchObject({ decision: "edit", text: "Use UV_CACHE_DIR." });
  });

  it("rejects and refreshes the queue", async () => {
    let reviewed = false;
    const calls = await openRun((url, init) => {
      if (url.endsWith("/review")) {
        reviewed = true;
        return detail(run(1, { status: "rejected" }));
      }
      if (url.includes("status=pending")) return reviewed ? [] : [run(1)];
      return url.endsWith("/1") && !init?.method ? detail(run(1)) : [];
    });

    await userEvent.click(screen.getByRole("button", { name: "Reject" }));

    expect(await screen.findByText("Nothing pending right now.")).toBeInTheDocument();
    expect(calls.filter((c) => c.url.includes("status=pending"))).toHaveLength(2);
  });

  it("flags runs that need attention", async () => {
    await openRun((url) =>
      url.endsWith("/api/runs/astral-sh/uv/1")
        ? detail(run(1, { needs_attention: true }))
        : [run(1, { needs_attention: true })],
    );

    expect(screen.getByRole("note")).toHaveTextContent("Needs attention");
  });
});
