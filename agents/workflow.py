"""Workflow agent — executes the actual reset/unlock against mock AD.

This agent uses Claude's tool-use feature so it decides WHICH action to
take based on state, rather than us hardcoding the mapping.
"""
from datetime import datetime, timezone
from anthropic import Anthropic

from config import CONFIG
from state import AgentState
from tools.mock_ad import (
    reset_password,
    unlock_account,
    check_cooldown,
)

_client = Anthropic(api_key=CONFIG.anthropic_api_key)


TOOLS = [
    {
        "name": "reset_password",
        "description": "Reset a student's SJSUOne password and send a temp link to their recovery email. Only call after identity is verified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "student_id": {"type": "string", "description": "9-digit SJSU ID"},
            },
            "required": ["student_id"],
        },
    },
    {
        "name": "unlock_account",
        "description": "Unlock a locked-out account. Fails if account is still within the 21-minute cooldown window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "student_id": {"type": "string"},
            },
            "required": ["student_id"],
        },
    },
    {
        "name": "check_cooldown",
        "description": "Check if a locked account is still in its 21-minute cooldown. Returns the time remaining.",
        "input_schema": {
            "type": "object",
            "properties": {
                "student_id": {"type": "string"},
            },
            "required": ["student_id"],
        },
    },
]


WORKFLOW_SYSTEM = """You are the Workflow agent for SJSU password resets.

The user's identity is already verified. Based on their intent:
- intent=forgot_password → call reset_password
- intent=locked_out → call check_cooldown first; if still in cooldown, tell the
  user to wait. If cooldown is over, call unlock_account.

Be direct. One tool call, then summarize the outcome for the user in 1-2 sentences.
"""


def _dispatch_tool(name: str, args: dict) -> dict:
    if name == "reset_password":
        return reset_password(args["student_id"])
    if name == "unlock_account":
        return unlock_account(args["student_id"])
    if name == "check_cooldown":
        return check_cooldown(args["student_id"])
    return {"success": False, "detail": f"unknown tool: {name}"}


def workflow_node(state: AgentState) -> dict:
    """Let Claude pick a tool and execute it."""
    if not state.get("identity_verified"):
        msg = "I can't run a password action without verifying your identity first."
        return {
            "status": "verification_failed",
            "messages": [{"role": "assistant", "content": msg}],
        }

    context = (
        f"Verified student_id: {state['student_id']}. "
        f"Intent: {state['intent']}."
    )

    response = _client.messages.create(
        model=CONFIG.model,
        max_tokens=CONFIG.max_tokens,
        system=WORKFLOW_SYSTEM,
        tools=TOOLS,
        messages=[{"role": "user", "content": context}],
    )

    # Execute any tool calls Claude requested
    tool_results = []
    for block in response.content:
        if block.type == "tool_use":
            result = _dispatch_tool(block.name, block.input)
            tool_results.append({"tool": block.name, "result": result})

    # Follow-up: give Claude the tool result so it can write the user-facing reply
    if tool_results:
        followup = _client.messages.create(
            model=CONFIG.model,
            max_tokens=CONFIG.max_tokens,
            system=WORKFLOW_SYSTEM,
            messages=[
                {"role": "user", "content": context},
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": next(b.id for b in response.content if b.type == "tool_use"),
                            "content": str(tool_results[0]["result"]),
                        }
                    ],
                },
            ],
        )
        user_reply = followup.content[0].text
        action_result = tool_results[0]["result"]
        action_taken = tool_results[0]["tool"]
    else:
        user_reply = response.content[0].text
        action_result = None
        action_taken = None

    # Decide status: resolved if the tool succeeded, else escalate
    if action_result and action_result.get("success"):
        status = "resolved"
    elif action_result and not action_result.get("success"):
        status = "escalated"
    else:
        status = "in_progress"

    return {
        "action_taken": action_taken,
        "action_result": action_result,
        "status": status,
        "messages": [{"role": "assistant", "content": user_reply}],
    }