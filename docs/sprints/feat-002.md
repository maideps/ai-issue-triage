# Sprint Contract

**Feature ID:** feat-002
**Feature Name:** GitHub issue ingestion
**Date:** 2026-10-27
**Estimated Duration:** 1 session

## What I Will Build

An issue-source interface in the domain, a GitHub GraphQL implementation in infrastructure, a pseudonymizer for usernames and @mentions, a JSONL store with a resume cursor, an ingestion use case in services, and a CLI (`python -m issue_triage ingest owner/repo`) that fetches issues with labels, state reason, `duplicateOf` and comments into the git-ignored `data/` folder.

## How I Will Verify

- [ ] Layer 1 (static): ruff and mypy --strict clean
- [ ] Layer 2 (runtime): tests with recorded GraphQL responses cover parsing, pagination, resume after interruption, rate-limit waiting, retry on transient errors and pseudonymization
- [ ] Layer 3 (system): `npm run check` passes; a real run against astral-sh/uv stores issues locally

## What Success Looks Like

Running the CLI twice continues from the saved cursor instead of starting over; no raw GitHub login appears in stored data; the layer rules still hold.

## Dependencies

- [x] Depends on: feat-001 (passing)
- [ ] Blocked by: none

## Risks

- Issues with more than 50 comments are truncated to the first 50; the stored record keeps the total count so later features can tell.
- GitHub GraphQL rate limits; the source reads `rateLimit` from every response and waits for the reset when the budget runs low.

## Commit Plan

One commit with code, tests, docs and harness state, then push.

---

_Signed off by: Claude (Opus 5.5) on 2026-10-27_
