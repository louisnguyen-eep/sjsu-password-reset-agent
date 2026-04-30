"""CLI entry point — runs an interactive chat loop and prints an agent trace.

See which agent is firing, which tool it called, and how state evolves.
Move to Streamlit, render this same trace in the right pane.
"""

from dotenv import load_dotenv
load_dotenv()

import logging
logging.basicConfig(
    filename='demo.log',
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s'
)

from graph import build_graph
from state import AgentState


def _print_trace(step: dict) -> None:
    """Pretty-print the state delta from a single graph step."""
    for node_name, delta in step.items():
        print(f"\n  ┌─ {node_name} ─" + "─" * 40)
        for k, v in delta.items():
            if k == "messages":
                for m in v:
                    print(f"  │  reply → {m['content'][:80]}")
            else:
                print(f"  │  {k} = {v}")
        print("  └" + "─" * 52)


def run():
    graph = build_graph()
    state: AgentState = {
        "messages": [],
        "verification_attempts": 0,
        "identity_verified": False,
        "status": "in_progress",
    }

    print("SJSU IT Password Reset Assistant")
    print("Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in {"quit", "exit"}:
            break
        if not user_input:
            continue

        # Append user message to running state
        state["messages"] = state["messages"] + [
            {"role": "user", "content": user_input}
        ]

        # Stream the graph so we can show the agent trace live
        final_state = state
        for step in graph.stream(state, stream_mode="updates"):
            _print_trace(step)
            # Merge updates into running state
            for node_delta in step.values():
                for k, v in node_delta.items():
                    if k == "messages":
                        final_state["messages"] = final_state["messages"] + v
                    else:
                        final_state[k] = v

        # Print the last assistant message as the "headline" reply
        last_assistant = next(
            (m for m in reversed(final_state["messages"]) if m["role"] == "assistant"),
            None,
        )
        if last_assistant:
            print(f"\nAssistant: {last_assistant['content']}\n")

        state = final_state

        # If we reached a terminal state, reset for a new session
        if state.get("status") in {"resolved", "escalated"}:
            print("--- session complete ---\n")
            state = {
                "messages": [],
                "verification_attempts": 0,
                "identity_verified": False,
                "status": "in_progress",
            }


if __name__ == "__main__":
    run()