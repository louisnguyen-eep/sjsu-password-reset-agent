"""Intake agent — classifies user intent and verifies SJSU student identity.

Design note: we make TWO separate Claude calls here (classify, then verify)
rather than one combined call. This keeps the security-critical verification
logic auditable and lets us swap in a deterministic rule-based classifier
later without touching verification.
"""
import json
import re
from anthropic import Anthropic

from config import CONFIG
from state import AgentState
from tools.mock_ad import lookup_student

_client = Anthropic(api_key=CONFIG.anthropic_api_key)


CLASSIFIER_SYSTEM = """You are the Intake classifier for an SJSU IT password-reset assistant.

Given the user's message, return JSON with this exact shape and nothing else:
{"intent": "<one of: forgot_password | locked_out | account_not_found | policy_question | out_of_scope | unclear>",
 "student_id": "<9-digit string or null>",
 "reasoning": "<one sentence>"}

Rules:
- "forgot_password": user says they forgot / can't remember / need to reset.
- "locked_out": user says they're locked out, account locked, too many attempts.
- "policy_question": user asks HOW something works, password rules, process — no reset wanted yet.
- "out_of_scope": not about passwords (wifi, email config, installing software, etc.).
- "unclear": intent genuinely ambiguous — ask a follow-up.
- Extract student_id ONLY if the user provides a 9-digit number. Otherwise null.
- Do NOT invent a student_id from partial numbers.
"""


def _extract_json(text: str) -> dict:
    """Claude sometimes wraps JSON in prose. Extract the first {...} block."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"intent": "unclear", "student_id": None, "reasoning": "parse_failed"}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {"intent": "unclear", "student_id": None, "reasoning": "parse_failed"}


def intake_node(state: AgentState) -> dict:
    """Entry node. Classifies the latest user message and attempts verification."""
    last_user_msg = next(
        (m["content"] for m in reversed(state["messages"]) if m["role"] == "user"),
        "",
    )

    # Build a small context block from prior conversation so the classifier
    # can resolve follow-up messages like a bare student ID after we asked for one.
    prior_intent = state.get("intent")
    recent_messages = state["messages"][-4:]  # last 4 turns max
    context_block = ""
    if len(recent_messages) > 1:
        history_lines = []
        for m in recent_messages[:-1]:  # exclude the current message
            role = "User" if m["role"] == "user" else "Assistant"
            history_lines.append(f"{role}: {m['content']}")
        context_block = (
            "Recent conversation (for context — classify the LATEST user message):\n"
            + "\n".join(history_lines)
            + f"\n\nPrior classified intent: {prior_intent or 'none'}\n\n"
            + "Latest user message: "
        )

    classifier_input = context_block + last_user_msg

    # --- Step 1: classify -----------------------------------------------------
    response = _client.messages.create(
        model=CONFIG.model,
        max_tokens=CONFIG.max_tokens,
        system=CLASSIFIER_SYSTEM,
        messages=[{"role": "user", "content": classifier_input}],
    )
    parsed = _extract_json(response.content[0].text)
    intent = parsed.get("intent", "unclear")
    student_id = parsed.get("student_id") or state.get("student_id")

    # --- Step 2: verify identity (only when we have an ID and an actionable intent)
    identity_verified = state.get("identity_verified", False)
    attempts = state.get("verification_attempts", 0)
    assistant_msg = ""

    needs_verification = intent in ("forgot_password", "locked_out", "account_not_found")

    if needs_verification and student_id and not identity_verified:
        student = lookup_student(student_id)
        if student is None:
            attempts += 1
            assistant_msg = (
                f"I couldn't find an active SJSU account for ID {student_id}. "
                "Please double-check your 9-digit ID."
            )
            intent = "account_not_found"
        else:
            # In a real system this is where MFA / security questions happen.
            # For the demo we accept the ID lookup as verification.
            identity_verified = True
            assistant_msg = f"Thanks {student['first_name']}, I've located your account."
    elif needs_verification and not student_id:
        assistant_msg = (
            "To help with a password reset I'll need your 9-digit SJSU ID. "
            "You can find it on the back of your Tower Card."
        )
    elif intent == "policy_question":
        assistant_msg = ""  # Knowledge agent will answer
    elif intent == "out_of_scope":
        assistant_msg = (
            "I only handle SJSUOne password issues. For other IT help, "
            "please open an iSupport ticket at isupport.sjsu.edu."
        )
    elif intent == "unclear":
        assistant_msg = (
            "Could you tell me a bit more? Are you trying to reset a forgotten "
            "password, or are you locked out of your account?"
        )

    update = {
        "intent": intent,
        "student_id": student_id,
        "identity_verified": identity_verified,
        "verification_attempts": attempts,
    }
    if assistant_msg:
        update["messages"] = [{"role": "assistant", "content": assistant_msg}]
    return update