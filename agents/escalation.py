"""Escalation agent — opens a Jira ticket via MCP when the system can't resolve.

Escalation triggers:
- verification failed (too many attempts)
- workflow tool returned success=False
- intent=out_of_scope and user insisted on help
- alumni / cutoff edge cases from Workflow
"""
from config import CONFIG
from state import AgentState
from tools.jira_mcp import create_ticket


def _build_summary(state: AgentState) -> tuple[str, str]:
    sid = state.get("student_id", "unknown")
    intent = state.get("intent", "unknown")
    reason = state.get("escalation_reason") or (
        state.get("action_result", {}).get("detail") if state.get("action_result") else None
    ) or "Automated flow could not resolve the request."

    summary = f"[Auto] Password assistance for student {sid} — {intent}"
    description = (
        f"Escalated by SJSU password-reset agent.\n\n"
        f"Student ID: {sid}\n"
        f"Classified intent: {intent}\n"
        f"Verification attempts: {state.get('verification_attempts', 0)}\n"
        f"Identity verified: {state.get('identity_verified', False)}\n"
        f"Last action: {state.get('action_taken')}\n"
        f"Action result: {state.get('action_result')}\n\n"
        f"Reason for escalation: {reason}\n\n"
        f"--- Conversation transcript ---\n"
    )
    for m in state.get("messages", []):
        description += f"{m['role']}: {m['content']}\n"

    return summary, description


def escalation_node(state: AgentState) -> dict:
    summary, description = _build_summary(state)

    ticket = create_ticket(
        project_key=CONFIG.jira_project_key,
        queue=CONFIG.escalation_queue,
        summary=summary,
        description=description,
    )

    user_msg = (
        f"I've opened ticket {ticket['ticket_id']} with SJSU IT support. "
        f"A technician will follow up via your recovery email within one business day. "
        f"You can track it at isupport.sjsu.edu."
    )

    return {
        "ticket_id": ticket["ticket_id"],
        "status": "escalated",
        "messages": [{"role": "assistant", "content": user_msg}],
    }