"""Agent state and the typed contracts between nodes.

The state is a plain dict so the Postgres checkpointer can store it; every value a node
writes is the ``model_dump()`` of one of the schemas below, and the reading node validates
it back. Hand-offs are data, never free text.
"""

from typing import Literal, TypedDict

from pydantic import BaseModel, Field

Action = Literal["duplicate", "answer", "needs_info", "summary"]
ACTIONS: tuple[Action, ...] = ("duplicate", "answer", "needs_info", "summary")


class GuardOut(BaseModel):
    flagged: bool
    signals: list[str] = Field(default_factory=list)
    redactions: dict[str, int] = Field(default_factory=dict)


class TriageOut(BaseModel):
    label: str
    confidence: float
    rationale: str = ""


class SimilarIssue(BaseModel):
    number: int
    similarity: float


class RetrievalOut(BaseModel):
    similar: list[SimilarIssue] = Field(default_factory=list)


class Decision(BaseModel):
    action: Action
    reason: str


class Proposal(BaseModel):
    action: Action
    text: str
    citations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    needs_attention: bool = False  # flagged, ungrounded or unsure: review it first


class Review(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    text: str | None = None  # the edited reply, required for "edit"
    reviewer: str = "maintainer"


class Outcome(BaseModel):
    status: Literal["approved", "rejected"]
    final_text: str | None
    edited: bool


class AgentState(TypedDict, total=False):
    repository: str
    number: int
    title: str
    body: str
    as_of: str  # ISO timestamp; only earlier issues are visible (D-004)
    guard: dict[str, object]
    triage: dict[str, object]
    retrieval: dict[str, object]
    decision: dict[str, object]
    proposal: dict[str, object]
    review: dict[str, object]
    outcome: dict[str, object]
