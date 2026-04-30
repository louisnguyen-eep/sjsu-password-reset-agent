"""End-to-end tests for the four demo scenarios.

Run before any presentation:
    python3.14 -m pytest test_demo.py -v

Each test runs a full LangGraph turn and asserts on the resulting state.
A green run = your demo will work.
"""
import pytest
from dotenv import load_dotenv
load_dotenv()

from graph import build_graph
from state import AgentState
from tools.mock_ad import seed as seed_mock_ad


# ---- Setup -----------------------------------------------------------------

@pytest.fixture(scope="module")
def graph():
    """Build the graph once for all tests."""
    return build_graph()


@pytest.fixture(autouse=True)
def reset_mock_ad():
    """Reset the mock AD database before every test so cooldown tests
    don't bleed into each other."""
    seed_mock_ad()


def _fresh_state() -> AgentState:
    return {
        "messages": [],
        "verification_attempts": 0,
        "identity_verified": False,
        "status": "in_progress",
    }


def _run(graph, user_msg: str, prior_state: AgentState = None) -> tuple[AgentState, list]:
    """Run one user turn through the graph. Returns (final_state, trace)."""
    state = prior_state or _fresh_state()
    state["messages"] = state["messages"] + [{"role": "user", "content": user_msg}]
    trace = []

    for step in graph.stream(state, stream_mode="updates"):
        for node_name, delta in step.items():
            trace.append(node_name)
            for k, v in delta.items():
                if k == "messages":
                    state["messages"] = state["messages"] + v
                else:
                    state[k] = v

    return state, trace


# ---- Scenario tests --------------------------------------------------------

def test_happy_path_password_reset(graph):
    """Maya forgets her password — should verify, route to workflow, reset succeeds."""
    state, trace = _run(graph, "I forgot my password, my ID is 012345678")

    assert "intake" in trace, "Intake agent should have fired"
    assert "workflow" in trace, "Workflow agent should have fired"
    assert state["intent"] == "forgot_password"
    assert state["student_id"] == "012345678"
    assert state["identity_verified"] is True
    assert state["action_taken"] == "reset_password"
    assert state["action_result"]["success"] is True
    assert state["status"] == "resolved"


def test_cooldown_path_locked_account(graph):
    """Diego is locked out — should call check_cooldown and report wait time."""
    state, trace = _run(graph, "I'm locked out, my ID is 123456789")

    assert "intake" in trace
    assert "workflow" in trace
    assert state["intent"] == "locked_out"
    assert state["identity_verified"] is True
    # Workflow should pick check_cooldown first because of the system prompt
    assert state["action_taken"] in ("check_cooldown", "unlock_account")
    # If cooldown is still active, action_result should reflect that
    if state["action_taken"] == "check_cooldown":
        assert state["action_result"]["in_cooldown"] is True
        assert state["action_result"]["minutes_remaining"] > 0


def test_escalation_path_inactive_account(graph):
    """Priya is inactive alumni — should escalate and create a Jira ticket."""
    state, trace = _run(graph, "Reset my password, ID 345678901")

    assert "intake" in trace
    assert "escalation" in trace
    assert state["intent"] == "account_not_found"
    assert state["identity_verified"] is False
    assert state["status"] == "escalated"
    assert state["ticket_id"] is not None
    assert state["ticket_id"].startswith("SJSUIT-")


def test_knowledge_path_policy_question(graph):
    """A pure RAG question should hit the Knowledge agent and retrieve docs."""
    state, trace = _run(graph, "What are the password requirements at SJSU?")

    assert "intake" in trace
    assert "knowledge" in trace
    assert state["intent"] == "policy_question"
    assert state["retrieved_docs"], "Knowledge agent should have retrieved at least one doc"
    assert state["grounded_answer"], "Knowledge agent should have produced an answer"
    # Make sure the answer actually cites a source
    last_msg = state["messages"][-1]["content"]
    assert "sjsu.edu" in last_msg.lower(), "Knowledge answer should cite a source URL"


def test_intake_asks_for_id_when_missing(graph):
    """User asks for a reset without giving ID — Intake should ask for it."""
    state, trace = _run(graph, "I forgot my password")

    assert "intake" in trace
    assert state["intent"] == "forgot_password"
    assert state["student_id"] is None
    assert state["identity_verified"] is False
    # Intake should reply asking for the ID
    last_msg = state["messages"][-1]["content"].lower()
    assert "id" in last_msg or "9-digit" in last_msg or "tower card" in last_msg


def test_out_of_scope_request(graph):
    """User asks about something unrelated — should be politely declined."""
    state, trace = _run(graph, "How do I install Microsoft Word?")

    assert "intake" in trace
    assert state["intent"] == "out_of_scope"
    # Should NOT have routed to workflow or knowledge
    assert "workflow" not in trace
    assert "knowledge" not in trace


# ---- Multi-turn / state preservation --------------------------------------

def test_multi_turn_id_provided_after_request(graph):
    """User asks for reset, Intake asks for ID, user provides ID — should
    proceed to workflow on the second turn."""
    # Turn 1: no ID
    state1, _ = _run(graph, "I need to reset my password")
    assert state1["student_id"] is None
    assert state1["identity_verified"] is False

    # Turn 2: provides ID, reusing prior state
    state2, trace2 = _run(graph, "012345678", prior_state=state1)

    assert "intake" in trace2
    assert state2["student_id"] == "012345678"
    assert state2["identity_verified"] is True