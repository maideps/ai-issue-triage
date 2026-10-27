# Product Overview

## Purpose

AI Issue Triage helps the people who maintain an issue tracker keep up with it. For every new issue it proposes a type label, points out likely duplicates with evidence, drafts a reply grounded in the project's documentation when the answer is already there, or asks for the missing information. A maintainer approves, edits or rejects each suggestion. The goal is to remove the repetitive reading and searching while every decision stays with a person.

The product is built and evaluated against the public history of [astral-sh/uv](https://github.com/astral-sh/uv), but the issue source sits behind an adapter so the same pipeline can serve Jira, Linear or a support inbox.

## Primary Users

- **Maintainer / support engineer** — works the triage queue: reviews suggestions and evidence, approves, edits or rejects.
- **Engineering lead** — watches quality and cost: triage accuracy, duplicate recall, approval rate, cost per issue.
- **Developer / integrator** — consumes the API, the MCP tools and the evaluation reports.

## Key User Flows

- A new issue arrives (sandbox webhook, or a replayed historical issue) and appears in the queue with a proposed type, confidence and suggested action.
- Open a suggestion: see the draft beside its evidence (similar past issues, cited doc sections) and approve, edit or reject it.
- Replay a historical uv issue "as of" its creation date and compare the system's suggestion with what the maintainers actually did.
- Run the evaluation suite and read the metrics per milestone: macro-F1, recall@k, faithfulness, cost and latency.

## Non-Goals

- Never posting to real repositories or contacting real maintainers or users; uv has a written AI policy and this project respects it.
- No routing by area: the ground truth is too sparse (8% of recent uv issues).
- No fine-tuning of large models; the only trained model is a small triage classifier.
- No multi-tenant accounts or production-grade access control in the first version.

## Product Constraints

- AI output is always a suggestion: nothing is posted without an explicit human approval, and only to an allowlisted sandbox repository.
- Every evaluation is time-split: no issue may be answered with data created after it.
- Raw GitHub data stays local (git-ignored); only a small sample and the scripts that fetch the rest are committed. Usernames are pseudonymized at ingestion.
- Tests and the default demo never call a real model; the LLM provider sits behind an interface with a deterministic fake.
- Secrets are read from environment variables and never committed.
