"""LangGraph StateGraph definition.

The router is the brain of the multi-agent system — it inspects state
after each node and decides where to go next. Drawing this graph in your
demo slides is a strong visual for the "orchestration" rubric item.
"""
from langgraph.graph import StateGraph, START, END

from state import AgentState
from config import CONFIG
from agents.intake import intake_node
from agents.knowledge import knowledge_node
from agents.workflow import workflow_node
from agents.escalation import escalation_node


def route_after_intake(state: AgentState) -> str:
    """Decide the next node based on intake output."""
    intent = state.get("intent")
    verified = state.get("identity_verified", False)
    attempts = state.get("verification_attempts", 0)

    # Hard escalate after max failed verifications
    if attempts >= CONFIG.max_verification_attempts:
        return "escalation"

    # Pure knowledge question
    if intent == "policy_question":
        return "knowledge"

    # Needs action AND identity is verified → workflow
    if intent in ("forgot_password", "locked_out") and verified:
        return "workflow"

    # Account doesn't exist or is inactive → escalate
    if intent == "account_not_found":
        return "escalation"

    # Out of scope — intake already replied, end the turn
    if intent == "out_of_scope":
        return END

    # Unclear or awaiting student ID → end turn, wait for user reply
    return END


def route_after_workflow(state: AgentState) -> str:
    """If the action failed, escalate; otherwise finish."""
    if state.get("status") == "escalated":
        return "escalation"
    return END


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("intake", intake_node)
    g.add_node("knowledge", knowledge_node)
    g.add_node("workflow", workflow_node)
    g.add_node("escalation", escalation_node)

    g.add_edge(START, "intake")

    g.add_conditional_edges(
        "intake",
        route_after_intake,
        {
            "knowledge": "knowledge",
            "workflow": "workflow",
            "escalation": "escalation",
            END: END,
        },
    )

    g.add_edge("knowledge", END)

    g.add_conditional_edges(
        "workflow",
        route_after_workflow,
        {
            "escalation": "escalation",
            END: END,
        },
    )

    g.add_edge("escalation", END)

    return g.compile()