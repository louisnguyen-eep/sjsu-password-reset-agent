# SJSU Password Reset — Multi-Agent AI System

A capstone project for BUS-118S that automates SJSUOne password resets end-to-end using a four-agent system built with LangGraph and Anthropic's Claude. The system handles identity verification, retrieves grounded answers from real SJSU IT documentation, executes resets against a mock directory, and escalates unresolvable cases to Jira via MCP.

> **Group 23 (Baddies)** — Louis Nguyen, Ryann' Clark, Maia Kerr, Christina Tra, Samantha Mier, Kern Dutta

---

## What it does

Roughly 20–40% of all enterprise IT tickets are password resets, each costing $15–70 to resolve manually. This system deflects the bulk of those tickets through a self-service AI assistant that's available 24/7, while safely escalating edge cases to humans.

A student opens the chat and types something like *"I forgot my password, my ID is 012345678."* The system classifies the intent, verifies the student against a directory, executes the reset autonomously, and confirms back to the user — typically in under 10 seconds. If the student is locked out, it enforces SJSU's real 21-minute cooldown. If the account is past the alumni cutoff, it files a Jira ticket for Tier-2 support with the full conversation transcript attached.

## Architecture

Four agents coordinated by a LangGraph state machine with conditional routing.

| Agent | Role |
|---|---|
| **Intake** | Classifies the user's request, extracts the SJSU ID, and verifies identity against the directory. Splits classification and verification into separate Claude calls so the security-critical step is auditable in isolation. |
| **Knowledge** | Retrieves the top-k most relevant chunks from a Voyage AI embedding index over scraped SJSU IT documentation, then asks Claude to answer using only that context. Cites source URLs. |
| **Workflow** | Uses Claude's tool-use feature to autonomously pick between `reset_password`, `unlock_account`, and `check_cooldown` based on intent. Enforces SJSU's 21-minute lockout policy and 24-month alumni cutoff. |
| **Escalation** | Files real Jira tickets via REST when the system can't resolve a request. Includes the full conversation transcript and reason for escalation. |

The router inspects state after each node and decides where to go next. Every routing decision is visible in the live agent trace pane during the demo.

## Tech stack

- **Orchestration:** [LangGraph](https://langchain-ai.github.io/langgraph/) — explicit state machine with conditional edges
- **LLM:** [Anthropic Claude](https://www.anthropic.com/api) — classification and autonomous tool selection
- **Embeddings:** [Voyage AI](https://www.voyageai.com/) `voyage-3-lite` — Anthropic's recommended embedding partner
- **Vector retrieval:** NumPy cosine similarity over a JSON-persisted embedding store
- **Mock directory:** SQLite with realistic SJSU rules (cooldown, alumni cutoff)
- **Issue tracking:** Jira Cloud REST API + Atlassian's official MCP server
- **UI:** [Streamlit](https://streamlit.io/) — chat on the left, live agent trace on the right
- **Testing:** pytest with end-to-end coverage of all four scenarios

## Project structure

```
sjsu_reset/
├── main.py              # CLI entry point
├── app.py               # Streamlit UI
├── graph.py             # LangGraph wiring + conditional router
├── state.py             # Shared AgentState definition
├── config.py            # Central configuration
├── draw_graph.py        # Generates the architecture diagram PNG
├── test_demo.py         # End-to-end pytest suite
├── agents/
│   ├── intake.py        # Classify + verify identity
│   ├── knowledge.py     # RAG retrieval + grounded answer
│   ├── workflow.py      # Tool-using executor
│   └── escalation.py    # Jira ticket creation
├── tools/
│   ├── mock_ad.py       # SQLite-backed fake student directory
│   └── jira_mcp.py      # Real Jira REST integration (with stub fallback)
└── knowledge/
    ├── scrape.py        # Fetches SJSU IT documentation pages
    ├── ingest.py        # Embeds chunks via Voyage AI
    └── retriever.py     # Cosine-similarity retrieval
```

## Setup

### Prerequisites

- Python 3.10 or newer (we developed on 3.14)
- An [Anthropic API key](https://console.anthropic.com/)
- A [Voyage AI API key](https://dashboard.voyageai.com/) — free tier works
- A [Jira Cloud workspace](https://www.atlassian.com/software/jira/free) with a project keyed `SJSUIT` (free)
- A [Jira API token](https://id.atlassian.com/manage-profile/security/api-tokens)

### Installation

```bash
# Clone and enter the project
git clone https://github.com/YOUR_USERNAME/sjsu-password-reset-agent.git
cd sjsu-password-reset-agent

# Install Python dependencies
python3 -m pip install -r requirements.txt
python3 -m pip install voyageai python-dotenv requests beautifulsoup4 streamlit pytest

# Configure environment variables
cp .env.example .env
# Then edit .env and fill in your keys

# Build the knowledge base (scrape + embed)
python3 -m knowledge.scrape
python3 -m knowledge.ingest

# Seed the mock student directory
python3 -m tools.mock_ad --seed
```

### Running the system

```bash
# Streamlit UI (recommended for demos)
python3 -m streamlit run app.py

# Or CLI mode
python3 main.py
```

### Running the tests

```bash
python3 -m pytest test_demo.py -v
```

All seven scenarios should pass: happy path, cooldown, escalation, knowledge/RAG, missing-ID prompt, out-of-scope handling, and multi-turn state preservation.

## Demo scenarios

The system has four demo paths that exercise different agent combinations.

| Scenario | Prompt | Path |
|---|---|---|
| **Happy path** | `I forgot my password, my ID is 012345678` | Intake → Workflow → reset succeeds |
| **Cooldown** | `I'm locked out, my ID is 123456789` | Intake → Workflow → tells user to wait |
| **Escalation** | `Reset my password, ID 345678901` | Intake → Escalation → real Jira ticket |
| **Knowledge / RAG** | `How do I set up Duo MFA?` | Intake → Knowledge → grounded answer with citation |

Switch to your Jira board during the escalation scenario to watch tickets land in real time.

## Design decisions worth calling out

**Why split Intake into two Claude calls?** The classifier and the identity verifier are conceptually different responsibilities. Splitting them makes verification auditable in isolation and lets us swap in deterministic verification (MFA, SMS code) without touching classification logic.

**Why NumPy instead of a vector database?** Our document corpus has ~50 chunks. A full vector database adds operational complexity without quality gains at this scale. Voyage AI handles the embedding, NumPy handles the similarity — the RAG quality is identical to what Pinecone or Chroma would deliver, with simpler infrastructure. The retriever interface is unchanged, so swapping in a real vector DB later is a 30-line code change.

**Why both Jira REST and Jira MCP?** The Escalation agent needs headless authentication, which fits Jira's REST API directly. The MCP integration is for VS Code, where Atlassian's official MCP server lets developers query the same Jira workspace interactively. Two clients, one workspace, one standardized protocol — that's MCP's value over per-client custom integrations.

**Why Claude tool-use instead of hardcoded tool selection?** The Workflow agent receives an intent and a verified student ID, and decides which of three tools to call. Hardcoding `if intent == "locked_out": call check_cooldown` works, but it's brittle as scenarios multiply. Claude's tool-use lets the agent reason about which tool fits the situation — closer to how a real IT support agent thinks.

## Validation

Seven end-to-end tests covering every routing path through the graph. All seven pass on each run, exercising real Claude API calls, real Voyage embedding lookups, and real Jira ticket creation. See `test_demo.py`.

## Acknowledgments

Built for Professor Murphy's BUS-118S Capstone, Spring 2026. Special thanks to the SJSU IT team whose published documentation became the grounding corpus for the Knowledge agent.

## License

Educational use only. Not affiliated with or endorsed by San José State University.
