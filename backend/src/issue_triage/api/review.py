"""Review queue API: list, inspect, enqueue and review triage proposals."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from issue_triage.domain.reviews import RunRecord, RunState
from issue_triage.services.review_queue import NotFound, NotPending, ReviewQueue, RunDetail

router = APIRouter(prefix="/api/runs", tags=["review"])


class RunOut(BaseModel):
    repository: str
    number: int
    title: str
    status: RunState
    action: str
    confidence: float
    needs_attention: bool
    proposal_text: str
    citations: list[str]
    evidence: dict[str, Any]
    final_text: str | None
    updated_at: datetime | None


class FeedbackOut(BaseModel):
    decision: str
    original_text: str
    final_text: str | None
    reviewer: str
    created_at: datetime | None


class RunDetailOut(BaseModel):
    run: RunOut
    issue: dict[str, Any]
    feedback: list[FeedbackOut]
    maintainer: dict[str, Any]
    agreement: dict[str, Any]


class ReviewIn(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    text: str | None = Field(default=None, max_length=20_000)
    reviewer: str = Field(default="maintainer", min_length=1, max_length=100)


def _run(run: RunRecord) -> RunOut:
    return RunOut(
        repository=run.repository,
        number=run.number,
        title=run.title,
        status=run.status,
        action=run.action,
        confidence=run.confidence,
        needs_attention=run.needs_attention,
        proposal_text=run.proposal_text,
        citations=list(run.citations),
        evidence=run.evidence,
        final_text=run.final_text,
        updated_at=run.updated_at,
    )


def _detail(detail: RunDetail) -> RunDetailOut:
    return RunDetailOut(
        run=_run(detail.run),
        issue={
            "title": detail.issue_title,
            "body": detail.issue_body,
            "created_at": detail.issue_created_at.isoformat(),
            "labels": list(detail.issue_labels),
        },
        feedback=[
            FeedbackOut(
                decision=f.decision,
                original_text=f.original_text,
                final_text=f.final_text,
                reviewer=f.reviewer,
                created_at=f.created_at,
            )
            for f in detail.feedback
        ],
        maintainer={
            "state": detail.maintainer.state,
            "state_reason": detail.maintainer.state_reason,
            "closed_at": detail.maintainer.closed_at.isoformat()
            if detail.maintainer.closed_at
            else None,
            "labels": list(detail.maintainer.labels),
            "type_label": detail.maintainer.type_label,
            "duplicate_of": detail.maintainer.duplicate_of,
            "needs_info": detail.maintainer.needs_info,
            "first_reply": detail.maintainer.first_reply,
        },
        agreement=detail.agreement,
    )


def queue(request: Request) -> ReviewQueue:
    provider = request.app.state.queue_provider
    if provider is None:
        raise HTTPException(503, "review queue is not configured")
    result: ReviewQueue = provider()
    return result


@router.get("", response_model=list[RunOut])
def list_runs(request: Request, status: RunState | None = None, limit: int = 100) -> list[RunOut]:
    return [_run(r) for r in queue(request).list(status, min(max(limit, 1), 500))]


@router.get("/{owner}/{name}/{number}", response_model=RunDetailOut)
def get_run(request: Request, owner: str, name: str, number: int) -> RunDetailOut:
    try:
        return _detail(queue(request).detail(f"{owner}/{name}", number))
    except NotFound as error:
        raise HTTPException(404, str(error)) from error


@router.post("/{owner}/{name}/{number}", response_model=RunDetailOut, status_code=201)
def enqueue_run(request: Request, owner: str, name: str, number: int) -> RunDetailOut:
    try:
        return _detail(queue(request).enqueue(f"{owner}/{name}", number))
    except NotFound as error:
        raise HTTPException(404, str(error)) from error


@router.post("/{owner}/{name}/{number}/review", response_model=RunDetailOut)
def review_run(
    request: Request, owner: str, name: str, number: int, body: ReviewIn
) -> RunDetailOut:
    if body.decision == "edit" and not (body.text and body.text.strip()):
        raise HTTPException(422, "an edit needs the edited text")
    try:
        detail = queue(request).review(
            f"{owner}/{name}", number, body.decision, body.text, body.reviewer
        )
    except NotFound as error:
        raise HTTPException(404, str(error)) from error
    except NotPending as error:
        raise HTTPException(409, str(error)) from error
    return _detail(detail)
