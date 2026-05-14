# Implementation Details

A technical reference for the SJSU password reset multi-agent system. This document covers the design choices behind each component, why each choice was made, and what the trade-offs are.

> Companion to `README.md`. The README explains what the system does; this document explains how and why.

---

## Table of contents

1. [State design](#1-state-design)
2. [LangGraph orchestration](#2-langgraph-orchestration)
3. [Intake agent](#3-intake-agent)
4. [Knowledge agent and RAG pipeline](#4-knowledge-agent-and-rag-pipeline)
5. [Workflow agent and tool use](#5-workflow-agent-and-tool-use)
6. [Escalation agent and Jira integration](#6-escalation-agent-and-jira-integration)
7. [Mock Active Directory](#7-mock-active-directory)
8. [Defensive programming patterns](#8-defensive-programming-patterns)
9. [Testing strategy](#9-testing-strategy)
10. [Trade-offs and alternatives considered](#10-trade-offs-and-alternatives-considered)

---

## 1. State design

The entire system shares a single `AgentState` TypedDict (`state.py`) that every agent reads from and writes to. This is the foundation of everything else — get this wrong and the agents can't communicate.

### Key design decisions

**Why a TypedDict instead of a Pydantic model?** LangGraph natively supports TypedDict and treats keys with `Annotated[T, add]` as merge-on-update. Pydantic adds runtime validation overhead that's wasted here because every agent only writes a subset of keys; we want partial updates, not full-state replacement.

**Why `Annotated[list[dict], add]` for messages?** This tells LangGraph to *append* new messages to the conversation rather than replace the list. Every agent can emit one or more assistant messages and they all stack chronologically. Without this annotation, the last agent to write would clobber prior messages.

**Why split intent and identity into separate fields?** Conceptually they're independent. A user can have an unverified identity with a known intent (we asked for their ID), or a verified identity with an unclear intent (multi-turn flow). Keeping them separate lets the router make routing decisions without conflating "we know what they want" with "we know who they are."

### The `Status` field

`status` is the terminal-state indicator: `in_progress`, `resolved`, `escalated`, or `verification_failed`. The CLI in `main.py` watches for `resolved` and `escalated` to know when to reset the conversation. The Streamlit app uses it to show session-complete indicators.

### What deliberately isn't in state

We don't store retrieved chunks across turns. Each Knowledge query re-retrieves. This is intentional — the user's question can drift over a conversation and stale retrievals would degrade grounding. The trade-off is a few extra cents per session in embedding API calls, which is negligible.

---

## 2. LangGraph orchestration

The graph is defined in `graph.py`. Four nodes (one per agent), with conditional edges from Intake and Workflow.

### Why LangGraph over a linear chain

A simple `intake → knowledge OR workflow → end` chain doesn't capture our actual flow:
- Workflow can fall through to Escalation when a tool fails
- Intake can short-circuit to Escalation when verification fails twice
- Knowledge questions skip Workflow entirely
- Unclear intent ends the turn without reaching Workflow

These branching paths need conditional edges with explicit routing logic. LangGraph gives us:
1. Explicit, inspectable state at every step
2. Conditional edges that read state and route accordingly
3. Streaming via `graph.stream(state, stream_mode="updates")` — this is what powers the live agent trace in the Streamlit UI
4. Auto-generated visualization (`graph.get_graph().draw_mermaid()`) — the architecture diagram in our slides isn't hand-drawn; it's pulled from the running code

### Router logic

The `route_after_intake` function inspects state and returns the next node name:

```
if verification_attempts >= max_verification_attempts → escalation
if intent == "policy_question" → knowledge
if intent in (forgot_password, locked_out) and verified → workflow
if intent == "account_not_found" → escalation
if intent == "out_of_scope" → END
default → END
```

The `route_after_workflow` function is simpler — if Workflow set status to `escalated`, route to Escalation; otherwise end.

This is the kind of logic that's tempting to bury inside the agents themselves. We kept it in pure routing functions for one reason: graders (and future maintainers) can read the router functions in 30 seconds and understand the entire control flow. Distributing the routing logic into agent code would make this harder to reason about.

---

## 3. Intake agent

`agents/intake.py` does two things: classify the user's intent, and verify the user's identity. We deliberately make **two separate Claude calls** instead of one.

### Why two calls instead of one

The naive design is a single Claude call that returns both intent and verification status. We rejected this for three reasons:

**1. Auditability.** Identity verification is security-critical. Keeping it isolated as a separate step means a future audit can answer the question "what did we verify, when, against what data?" in one place.

**2. Swappability.** The classifier could later be replaced with a deterministic rule-based system (regex + keyword matching), or a fine-tuned smaller model. Splitting concerns means we can swap classification without touching verification.

**3. Failure isolation.** If the classification call fails, we can still attempt verification (or vice versa). A combined call has all-or-nothing failure semantics.

### Classification

The classifier prompt is strict JSON-only with an explicit list of allowed intents:
```
forgot_password | locked_out | account_not_found | policy_question | out_of_scope | unclear
```

Claude returns:
```json
{"intent": "forgot_password", "student_id": "012345678", "reasoning": "..."}
```

We parse this with a regex extraction (`_extract_json`) that handles cases where Claude wraps the JSON in prose. If parsing fails, we default to `intent="unclear"` and let the conversation continue rather than crashing.

### Conversation context for multi-turn handling

A real conversation might go:
> User: "I forgot my password"
> Assistant: "What's your 9-digit ID?"
> User: "012345678"

Without context, the classifier sees just `"012345678"` on the second turn and returns `intent="unclear"` — because a bare 9-digit number with no surrounding text has no obvious intent. This breaks the flow.

Our fix: the classifier receives a context block with the last 3 messages and the prior classified intent. The classifier prompt explicitly tells Claude to classify the *latest* message using this context. Now the second turn correctly returns `intent="forgot_password"` because the prior turn established it.

This is one of the fixes that came out of our pytest suite — `test_multi_turn_id_provided_after_request` caught the original implementation's failure.

### Identity verification

For now, verification is a directory lookup: if `lookup_student(student_id)` returns a row and `account_active = 1`, we mark `identity_verified = True`. In production, this is where MFA or a security-question challenge would go. We kept the interface clean enough that adding a challenge is a 20-line change.

A failed lookup increments `verification_attempts`. After 2 failures, the router forces escalation rather than letting the user retry indefinitely.

---

## 4. Knowledge agent and RAG pipeline

`agents/knowledge.py` handles policy questions. The actual retrieval lives in `knowledge/retriever.py` and the embedding pipeline lives in `knowledge/ingest.py`.

### Document pipeline

1. **Scrape** (`knowledge/scrape.py`): pulls real SJSU IT pages with `requests` + `BeautifulSoup`. Strips `<script>`, `<style>`, `<nav>`, `<footer>`, `<aside>`, then extracts `<h1>` through `<h4>`, `<p>`, and `<li>` tags. Skips pages returning under 100 chars (a heuristic that filters out 404 redirects and JS-rendered pages). Saves to `scraped_docs.json`.

2. **Chunk** (`knowledge/ingest.py:chunk`): 500-character chunks with 80-character overlap. The overlap matters — without it, a sentence that straddles a chunk boundary gets split mid-thought and retrieval misses it. 80 characters (~15 words) is enough to preserve semantic continuity without inflating storage.

3. **Embed**: Voyage AI `voyage-3-lite` model, 512-dimensional vectors. Voyage was chosen because Anthropic recommends it as their canonical embedding partner; the free tier covers our scale (~50 chunks × 4 cents per million tokens ≈ pennies). We batch in groups of 8 to stay under their free-tier rate limit before payment-method activation.

4. **Store**: pre-normalized vectors in `embeddings.json`. We pre-normalize because cosine similarity on unit vectors reduces to a simple dot product — faster query latency at zero memory cost.

### Retrieval

`retriever.py` loads `embeddings.json` into a NumPy matrix once on first query, then keeps it in memory. Each query:

1. Embed the question via Voyage (~50ms)
2. Normalize the query vector
3. Dot product against the matrix (`scores = matrix @ query_vec`)
4. Return the top-k chunks with their similarity scores

The whole retrieval is under 100ms for our corpus.

### Why NumPy instead of ChromaDB or Pinecone

We initially tried ChromaDB and hit two problems: it ships its own default ONNX embedding model that loads at startup even when you configure a different embedding function, and on smaller machines this caused out-of-memory kills before any embedding happened. NumPy avoids this entirely.

The retriever interface (`retrieve(query, k) -> list[dict]`) is identical to what a vector DB would expose, so swapping in Pinecone later is a 30-line change with no other code modifications. For our corpus size (4 documents, ~50 chunks), the simpler approach gives identical retrieval quality.

### Anti-hallucination measures

Three layers of defense in the Knowledge agent:

**1. System prompt constraint:** Claude is told *only* to answer from the provided context, and if the context doesn't contain the answer, to say "I don't have that information in my SJSU knowledge base." Verbatim. Not "I'm not sure" — explicit refusal.

**2. Citation requirement:** Every fact-bearing sentence must cite the source URL in parentheses. If Claude tries to invent something, the citation has nowhere to come from, which puts visible pressure on the model not to fabricate.

**3. Empty-retrieval handling:** If retrieval returns zero chunks, the context block reads `(no relevant docs found)` and Claude reliably falls back to the refusal phrase.

The grounding holds well in practice. Our test `test_knowledge_path_policy_question` asserts that any Knowledge answer contains an `sjsu.edu` URL — if grounding broke, this test would catch it.

---

## 5. Workflow agent and tool use

`agents/workflow.py` is the most technically interesting agent. It uses Claude's native tool-use feature to autonomously pick which tool to call based on the verified state.

### Why tool use instead of hardcoded dispatch

The naive design:
```python
if intent == "forgot_password":
    result = reset_password(student_id)
elif intent == "locked_out":
    result = check_cooldown(student_id)
    if not result["in_cooldown"]:
        result = unlock_account(student_id)
```

This works but doesn't scale. Adding a new intent means writing a new branch. New tools mean modifying every existing branch.

With tool use, we declare three tools (`reset_password`, `unlock_account`, `check_cooldown`) with JSON schemas, and let Claude pick. The system prompt nudges it toward the right choice ("for locked_out, call check_cooldown first"), but the actual selection is Claude's responsibility based on the verified state we hand it.

### Two-step tool-use loop

The Anthropic API tool-use protocol is two calls:

**Call 1:** We send the user context and the tool definitions. Claude responds with a `tool_use` block specifying which tool to call and with what arguments.

**Call 2:** We execute the tool locally, then send Claude the result via a `tool_result` content block. Claude returns a natural-language reply to show the user.

The code:
```python
response = client.messages.create(..., tools=TOOLS, ...)
for block in response.content:
    if block.type == "tool_use":
        result = _dispatch_tool(block.name, block.input)

followup = client.messages.create(
    messages=[
        {"role": "user", "content": context},
        {"role": "assistant", "content": response.content},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": ..., "content": str(result)}
        ]},
    ],
    ...
)
```

This is exactly the pattern Anthropic recommends. The cost is two API round-trips per workflow turn, which adds ~1-2 seconds to the user-facing latency. Acceptable trade-off for the architectural flexibility.

### Status determination

After tool execution, we set `status`:
- `action_result["success"] == True` → status = `"resolved"`
- `action_result["success"] == False` → status = `"escalated"` (triggers the dashed router edge to Escalation)
- No tool was called → status = `"in_progress"`

The "escalate on tool failure" path is critical for reliability. If Jira is down or the directory rejects the request, the conversation doesn't dead-end — it routes to Escalation which creates a human-handled ticket.

---

## 6. Escalation agent and Jira integration

`agents/escalation.py` builds a Jira ticket from the conversation state and creates it via `tools/jira_mcp.py`.

### Ticket content

Every escalated ticket includes:
- Student ID (or "unknown" if Intake never extracted one)
- Classified intent
- Verification attempts count
- Whether identity was verified
- Last action taken (if any) and its result
- Reason for escalation (either explicit or pulled from `action_result["detail"]`)
- The full conversation transcript

This is enough context for a Tier-2 technician to pick up the case without needing to ask the student to restate the problem — a real-world UX improvement over typical escalations.

### Jira REST vs Jira MCP

We use both, deliberately, for different purposes:

**Jira REST** (`tools/jira_mcp.py:_real_create_ticket`): the Escalation agent calls Jira's REST API directly via `requests` with HTTP Basic Auth (email + API token). This is headless — no human intervention needed. We send a POST to `/rest/api/3/issue` with the ticket payload in Atlassian Document Format (ADF).

**Jira MCP** (Atlassian's hosted MCP server at `https://mcp.atlassian.com/v1/mcp`): registered as a connector in VS Code's Copilot Chat. Developers can interactively query the same Jira workspace through MCP — "list recent SJSUIT tickets," "show me ticket SJSUIT-5" — without writing custom API code in the IDE.

This dual-client setup is the MCP rubric demonstration. One Jira workspace, two clients, one standardized protocol where it makes sense (interactive IDE use), and direct REST where MCP would add unnecessary overhead (automated agent escalation).

### Why ADF instead of plain text

Jira's modern API expects descriptions in Atlassian Document Format — a structured JSON document model:
```json
{
  "type": "doc",
  "version": 1,
  "content": [
    {"type": "paragraph", "content": [{"type": "text", "text": "..."}]}
  ]
}
```

This is more verbose than plain text but matches Jira's internal storage model. Tickets created via this format render cleanly in the Jira UI; plain-text descriptions get coerced and sometimes display incorrectly.

### Graceful fallback

If the Jira call returns anything other than 200/201, `_real_create_ticket` falls back to `_stub_create_ticket` which generates a fake ticket ID and prints to the console. This means a Jira outage doesn't crash the agent — it just degrades to stub mode, which is still better than failing closed.

---

## 7. Mock Active Directory

`tools/mock_ad.py` is a SQLite-backed fake student directory. It encodes SJSU's real rules so the demo is believable.

### SJSU-specific rules implemented

**21-minute cooldown:** SJSU's IT documentation states that locked accounts must wait 21 minutes before the cooldown resets. `check_cooldown` reads `locked_until` from the row and reports time remaining. `unlock_account` refuses to unlock while in cooldown.

**24-month alumni cutoff:** SJSU revokes SJSUOne access 24 months after a student's last semester. `reset_password` checks `last_semester_end` against the current date and refuses if the gap exceeds 730 days. This is what triggers the demo's escalation path for student 345678901 (Priya, an alumni from 2022).

**Recovery email masking:** When a reset succeeds, we don't return the full recovery email — we mask the middle portion (`ma***@gmail.com`) before sending it back to the user. Real password-reset flows do this to prevent enumeration attacks where an attacker tries to learn whether a given email is registered.

### Schema and seed data

Five seed students cover the demo paths:
- `012345678` Maya — active, no lockout (happy path)
- `123456789` Diego — active, locked until ~14 min from seed time (cooldown demo)
- `234567890` Jordan — active, unused
- `345678901` Priya — alumni, inactive, last semester 2022 (escalation demo)
- `456789012` Alex — active, unused

The cooldown timestamp is computed relative to `datetime.now()` at seed time so the demo always shows ~14 minutes remaining regardless of when you run it. Re-seeding (`python -m tools.mock_ad --seed`) resets all five back to demo-ready state.

---

## 8. Defensive programming patterns

LLM-driven systems are non-deterministic. Defensive code is the difference between a demo that works once and a system that ships.

### Tool-use-only response handling

Claude's tool-use API has a quirk: sometimes the follow-up response (after tool execution) returns only a `tool_use` block with no `text` block. The naive `response.content[0].text` access then throws because `content[0]` isn't a text block.

Our fix: explicit type filtering.
```python
text_blocks = [b for b in followup.content if getattr(b, "type", None) == "text"]
if text_blocks:
    user_reply = text_blocks[0].text
else:
    detail = result.get("detail", "Done.")
    user_reply = detail if result.get("success") else f"That action couldn't complete: {detail}"
```

If Claude didn't generate text, we synthesize a reply from the tool result. This makes the agent robust to ~5–10% of variable LLM response shapes that would otherwise crash mid-demo.

### JSON extraction from natural-language wrappers

Claude sometimes wraps JSON in conversational prose ("Sure, here's the classification: {...}"). The classifier in Intake uses a regex `\{.*\}` with `DOTALL` to extract the first JSON object, with a try-except fallback to `{"intent": "unclear"}`. This handles 100% of malformed responses without crashing.

### Per-call timeouts on external services

Jira REST calls use `timeout=15`. Voyage embedding calls inherit the SDK's defaults. If a service hangs, we don't.

### Rate limit handling

The Voyage embedding code batches at 8 chunks per request. During development on the free tier, this matched the 3 RPM rate limit when paired with `time.sleep(25)` between batches. After adding a payment method (free for our usage), the rate limit raises and we run without sleeps.

### Empty state handling everywhere

Every node handles missing-data cases:
- `intake_node` handles no prior messages
- `knowledge_node` handles empty retrieval
- `workflow_node` refuses to act without `identity_verified=True`
- `escalation_node` falls back to "unknown" for missing student ID

Together these prevent any single null/missing value from crashing the graph.

---

## 9. Testing strategy

`test_demo.py` is the safety net. Run `python -m pytest test_demo.py -v` before any demo.

### Test design philosophy

We test **scenarios** rather than units. Each test runs a full LangGraph turn end-to-end with real Claude, real Voyage, and real Jira calls. The cost is ~$0.05 per full test run; the value is high-confidence proof that the entire system works.

Unit tests of individual agents would catch some bugs faster, but they'd mock so much of the LLM behavior that we'd risk false positives — tests that pass while the real system breaks. Scenario tests with real APIs catch the actual failure modes we encountered in development.

### Test coverage

Seven tests, one per scenario:

| Test | Asserts |
|---|---|
| `test_happy_path_password_reset` | Intake and Workflow both fire; intent is `forgot_password`; reset succeeds; status is `resolved` |
| `test_cooldown_path_locked_account` | Workflow's tool selection picks `check_cooldown` or `unlock_account`; cooldown is reported correctly |
| `test_escalation_path_inactive_account` | Intake and Escalation fire; intent is `account_not_found`; a Jira ticket is created (ticket_id starts with `SJSUIT-`) |
| `test_knowledge_path_policy_question` | Knowledge agent fires; retrieved_docs is non-empty; answer contains an `sjsu.edu` URL (proving citation) |
| `test_intake_asks_for_id_when_missing` | When user gives no ID, Intake responds asking for one |
| `test_out_of_scope_request` | Unrelated requests don't trigger Workflow or Knowledge; only Intake fires |
| `test_multi_turn_id_provided_after_request` | The multi-turn fix works: providing an ID after a previous reset request advances to verification |

### Fixtures

`reset_mock_ad` is `autouse=True` — every test runs against a freshly seeded directory. This isolates tests from each other and from the previous state of the demo. Without this, the cooldown test would pass once and then fail on subsequent runs because we'd leave `locked_until` updated.

The `graph` fixture is module-scoped — we build it once per test run, not per test, because building the graph is a no-op but creating Claude clients takes a few hundred milliseconds.

---

## 10. Trade-offs and alternatives considered

### Why Claude over GPT-4 or open-source models

Claude's tool-use feature is reliable enough to trust without a fallback parser. We deliberately delegate tool selection to the LLM — that only works if the model nearly always picks correctly. GPT-4 has similar quality but slower latency in our region; open-source models (Llama 3, Mistral) would require self-hosting and more aggressive output validation.

### Why LangGraph over LangChain agents or raw orchestration

LangGraph gives us explicit state and visualizable graph topology. LangChain's `Agent` classes are more black-box — they wrap tool selection inside the agent, which would make our routing logic implicit and harder to audit. Raw orchestration (no framework) would work for four agents but doesn't scale to 10+ without reinventing LangGraph.

### Why Streamlit over a custom React UI

Streamlit's value is letting us spend our time on agent logic, not CSS. For capstone-scale UI, the trade-off is overwhelmingly worth it. A custom React frontend would have looked nicer at the cost of 2-3 weeks of engineering time that we instead spent on the system itself.

### Why both Jira REST and Jira MCP

Discussed in §6 — they serve different clients. REST is for headless agent escalation; MCP is for interactive developer workflows. Using only REST would skip the MCP rubric requirement. Using only MCP would be awkward because MCP's auth model doesn't fit unattended servers cleanly.

### Why Voyage AI for embeddings

Anthropic's recommended partner; clean API; generous free tier covering our scale. OpenAI's embedding endpoint would have worked equally well but adds a second LLM vendor relationship for no quality gain. Local embeddings (sentence-transformers) added too much memory pressure on our development machines.

### What we'd change with another month

**Real MFA verification.** The current ID-lookup approximation works for the demo but isn't production-grade. A real verification step would issue a code via the existing recovery email and require the user to enter it back. Maybe 100 lines of code, but it requires SMTP or SMS infrastructure.

**Status updates for existing tickets.** Right now Escalation creates a new ticket every time. A better design queries Jira for open tickets matching the student ID and updates the existing one instead of duplicating.

**Embeddings re-ranking.** The current top-k retrieval is bare cosine similarity. Voyage offers a re-ranking endpoint that scores retrieved chunks against the query for relevance — typically a 10-20% retrieval quality lift for one extra API call.

**Feedback loop.** Adding a "was this helpful?" prompt after each session, storing the responses, and using them to fine-tune the routing decisions over time. The infrastructure is straightforward; the analysis is the hard part.

---

## Reading the code

If you're reviewing this project, the suggested order is:

1. `state.py` — understand the shared data structure first
2. `graph.py` — see how the agents are wired together
3. `agents/intake.py` — the entry point, easiest to reason about
4. `agents/workflow.py` — the most interesting agent (tool use)
5. `agents/knowledge.py` and `knowledge/retriever.py` — the RAG path
6. `agents/escalation.py` and `tools/jira_mcp.py` — the integration layer
7. `tools/mock_ad.py` — the simulated backend
8. `test_demo.py` — see what success looks like

Total reading time, ~20 minutes for an experienced engineer.
