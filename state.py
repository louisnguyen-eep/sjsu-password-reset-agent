"""Shared AgentState — every LangGraph node reads from and writes to this."""
from typing import TypedDict, Annotated, Literal, Optional
from operator import add


# Allowed intents — keep this list small and meaningful. Intake classifier
# MUST return one of these exact strings.
Intent = Literal[
    "forgot_password",      # user knows their ID, needs a reset
    "locked_out",           # too many bad attempts, needs unlock + wait
    "account_not_found",    # ID doesn't exist or is inactive
    "policy_question",      # "how do I change my password?" — pure RAG
    "out_of_scope",         # not a password issue — escalate or decline
    "unclear",              # need to ask follow-up
]

Status = Literal[
    "in_progress",
    "resolved",
    "escalated",
    "verification_failed",
]


class AgentState(TypedDict, total=False):
    # Conversation — appended to by every node via operator.add
    messages: Annotated[list[dict], add]

    # Intake agent outputs
    intent: Intent
    student_id: Optional[str]           # 9-digit SJSU ID if provided
    identity_verified: bool
    verification_attempts: int

    # Knowledge agent outputs
    retrieved_docs: list[dict]          # [{content, source, score}]
    grounded_answer: Optional[str]

    # Workflow agent outputs
    action_taken: Optional[str]         # e.g. "password_reset", "unlock_scheduled"
    action_result: Optional[dict]       # {success: bool, detail: str, cooldown_until?: str}

    # Escalation agent outputs
    ticket_id: Optional[str]
    escalation_reason: Optional[str]

    # Overall
    status: Status
    next_agent: Optional[str]           # set by router, read by graph edges