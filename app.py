"""Streamlit UI — two-pane chat + agent trace.

Run: python3.14 -m streamlit run app.py
Opens a browser tab at http://localhost:8501
"""
from dotenv import load_dotenv
load_dotenv()

import streamlit as st

from graph import build_graph
from state import AgentState

import logging
logging.basicConfig(
    filename='demo.log',
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(message)s'
)

# --- Page setup -------------------------------------------------------------
st.set_page_config(
    page_title="SJSU Password Reset Agent",
    page_icon="🔑",
    layout="wide",
)

st.markdown(
    """
    <style>
    .agent-card {
        border-left: 4px solid #0055A2;
        background-color: #f5f7fa;
        padding: 12px 16px;
        margin-bottom: 10px;
        border-radius: 4px;
    }
    .agent-card h4 { margin: 0 0 6px 0; color: #0055A2; font-size: 14px; }
    .agent-card .kv { font-family: 'SF Mono', Monaco, monospace; font-size: 12px; color: #333; margin: 2px 0; }
    .agent-card .kv b { color: #0055A2; }
    .intake { border-left-color: #1D9E75; }
    .intake h4 { color: #1D9E75; }
    .intake .kv b { color: #1D9E75; }
    .knowledge { border-left-color: #378ADD; }
    .knowledge h4 { color: #378ADD; }
    .knowledge .kv b { color: #378ADD; }
    .workflow { border-left-color: #BA7517; }
    .workflow h4 { color: #BA7517; }
    .workflow .kv b { color: #BA7517; }
    .escalation { border-left-color: #E24B4A; }
    .escalation h4 { color: #E24B4A; }
    .escalation .kv b { color: #E24B4A; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --- Session state ----------------------------------------------------------
def _fresh_state() -> AgentState:
    return {
        "messages": [],
        "verification_attempts": 0,
        "identity_verified": False,
        "status": "in_progress",
    }


if "graph" not in st.session_state:
    st.session_state.graph = build_graph()
if "agent_state" not in st.session_state:
    st.session_state.agent_state = _fresh_state()
if "trace" not in st.session_state:
    st.session_state.trace = []  # list of {node, delta}


# --- Layout -----------------------------------------------------------------
st.title("🔑 SJSU Password Reset Assistant")
st.caption("Multi-agent AI system built with LangGraph, Claude, and Chroma RAG")

chat_col, trace_col = st.columns([3, 2], gap="large")


# ---- Chat pane -------------------------------------------------------------
with chat_col:
    st.subheader("Chat")

    # Render existing conversation
    for msg in st.session_state.agent_state["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Input at the bottom
    user_input = st.chat_input("Tell me what you need help with...")

    if user_input:
        # Render user message immediately
        with st.chat_message("user"):
            st.markdown(user_input)

        # Append to state
        st.session_state.agent_state["messages"] = (
            st.session_state.agent_state["messages"]
            + [{"role": "user", "content": user_input}]
        )

        # Run the graph and collect trace
        with st.spinner("Agents working..."):
            for step in st.session_state.graph.stream(
                st.session_state.agent_state, stream_mode="updates"
            ):
                for node_name, delta in step.items():
                    # Record trace entry
                    st.session_state.trace.append({"node": node_name, "delta": delta})

                    # Merge delta into agent state
                    for k, v in delta.items():
                        if k == "messages":
                            st.session_state.agent_state["messages"] = (
                                st.session_state.agent_state["messages"] + v
                            )
                        else:
                            st.session_state.agent_state[k] = v

        # Rerun so the new assistant messages render
        st.rerun()

    # Reset button
    if st.button("Start new session"):
        st.session_state.agent_state = _fresh_state()
        st.session_state.trace = []
        st.rerun()


# ---- Trace pane ------------------------------------------------------------
with trace_col:
    st.subheader("Agent trace")
    st.caption("Live view of which agent ran and what it decided")

    if not st.session_state.trace:
        st.info("Send a message to see the agents work.")
    else:
        # Show most recent first
        for i, entry in enumerate(reversed(st.session_state.trace)):
            node = entry["node"]
            delta = entry["delta"]
            step_num = len(st.session_state.trace) - i

            # Build a compact key-value list, skipping the noisy messages field
            kv_rows = []
            for k, v in delta.items():
                if k == "messages":
                    for m in v:
                        preview = m["content"][:90] + ("..." if len(m["content"]) > 90 else "")
                        kv_rows.append(f'<div class="kv"><b>reply →</b> {preview}</div>')
                elif k == "retrieved_docs":
                    kv_rows.append(
                        f'<div class="kv"><b>retrieved_docs</b> = {len(v)} chunks</div>'
                    )
                else:
                    kv_rows.append(f'<div class="kv"><b>{k}</b> = {v}</div>')

            kv_html = "".join(kv_rows)
            st.markdown(
                f"""
                <div class="agent-card {node}">
                    <h4>Step {step_num}: {node}</h4>
                    {kv_html}
                </div>
                """,
                unsafe_allow_html=True,
            )

    # Current state summary at the bottom
    with st.expander("Full current state"):
        display = {
            k: v for k, v in st.session_state.agent_state.items()
            if k != "messages"
        }
        st.json(display)


# ---- Sidebar: demo scenarios -----------------------------------------------
with st.sidebar:
    st.header("Demo scenarios")
    st.caption("Quick-run the three paths your presentation covers.")

    scenarios = [
        ("✅ Happy path", "I forgot my password, my ID is 012345678"),
        ("⏱️ Cooldown path", "I'm locked out, my ID is 123456789"),
        ("🚨 Escalation path", "Reset my password, ID 345678901"),
        ("📚 Knowledge path", "What services can I access with my SJSUOne password?"),
    ]

    for label, prompt in scenarios:
        if st.button(label, use_container_width=True):
            # Pre-fill input by injecting as user message
            st.session_state.agent_state["messages"] = (
                st.session_state.agent_state["messages"]
                + [{"role": "user", "content": prompt}]
            )

            with st.spinner("Running..."):
                for step in st.session_state.graph.stream(
                    st.session_state.agent_state, stream_mode="updates"
                ):
                    for node_name, delta in step.items():
                        st.session_state.trace.append({"node": node_name, "delta": delta})
                        for k, v in delta.items():
                            if k == "messages":
                                st.session_state.agent_state["messages"] = (
                                    st.session_state.agent_state["messages"] + v
                                )
                            else:
                                st.session_state.agent_state[k] = v
            st.rerun()

    st.divider()
    st.caption(
        "Each color in the trace maps to an agent: "
        "**green** = Intake, **blue** = Knowledge, "
        "**amber** = Workflow, **red** = Escalation."
    )