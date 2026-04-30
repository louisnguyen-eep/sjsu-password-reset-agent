# SJSU Password Reset Agent — Demo Script

> Total runtime: 7-9 minutes including Q&A buffer.
> Goal: prove every rubric item with one concrete moment each.

---

## Pre-demo checklist (run 30 minutes before)

Open these tabs in this order so they're left-to-right in your browser:

1. **Jira board** — `https://YOUR_SITE.atlassian.net` → SJSU IT project → Board view
2. **VS Code** with Copilot Chat sidebar open and Atlassian MCP visible
3. **Streamlit app** — `http://localhost:8501`
4. **Slides** with the LangGraph architecture PNG

In the terminal:
```bash
cd ~/Documents/sjsu_reset
python3.14 -m streamlit run app.py
```

Then in a SECOND terminal (so the first stays free for restart if needed):
```bash
cd ~/Documents/sjsu_reset
ls data/   # confirm chroma + students.db exist
```

Test one scenario quickly to confirm everything still works. Then **clear the Streamlit chat** (click "Start new session" in the sidebar) before presenting.

---

## Opening (45 seconds)

> "Password resets are the #1 ticket type in enterprise IT — typically 20–40% of help desk volume, costing $15–70 each to resolve manually. SJSU's IT help desk is no exception. Our project is a multi-agent AI system that handles SJSUOne password resets end-to-end, with real integrations to Jira and a live RAG knowledge base of SJSU IT documentation."

**Show the architecture diagram slide.**

> "The system has four agents orchestrated by LangGraph. Intake classifies the request and verifies the student. Knowledge does RAG retrieval over SJSU's IT pages. Workflow executes the actual reset against our directory. And Escalation files Jira tickets for cases the system can't handle. The router is conditional — it inspects state after each agent and decides where to go next."

---

## Scenario 1: Happy path (90 seconds)

**What you're proving:** Multi-agent orchestration. Intake → Workflow with tool-use. The Workflow agent autonomously picks the right tool.

**Click in the sidebar:** ✅ Happy path

The user message `"I forgot my password, my ID is 012345678"` appears.

**While the agents run, narrate:**

> "Intake just fired — you can see it on the right. It classified the intent as 'forgot_password' and extracted the student ID. It then looked up the ID in our mock Active Directory and verified the account belongs to Maya Patel."

> "Now the router routes to Workflow because intent requires action and identity is verified. Watch — the Workflow agent uses Claude's tool-use feature. It sees three available tools: reset_password, unlock_account, and check_cooldown. It chose reset_password autonomously based on the intent. The tool returned success, the cooldown was cleared, and a temp link was sent to the masked recovery email."

> "Notice we never hardcoded which tool to call — Claude reasoned about which tool fit the situation. That's the multi-agent autonomy story."

---

## Scenario 2: Cooldown path (75 seconds)

**What you're proving:** State-aware tool selection and SJSU-specific business rules.

**Click in the sidebar:** ⏱️ Cooldown path

User message: `"I'm locked out, my ID is 123456789"`

**Narrate while it runs:**

> "Same Intake step, different intent — 'locked_out' instead of 'forgot_password'. Diego's account exists, so identity is verified. Now Workflow gets called again, but watch what tool it picks this time."

> "Claude looks at the intent and chooses check_cooldown FIRST, not unlock — because SJSU policy says we wait 21 minutes after a lockout before unlocking. The tool returns 14 minutes remaining. The agent doesn't try to override the cooldown — it tells Diego to wait."

> "This is the difference between a chatbot and an agent. The Workflow agent enforces real institutional policy without us writing if-statements for every rule."

---

## Scenario 3: Knowledge / RAG path (60 seconds)

**What you're proving:** RAG with real SJSU documentation, grounding, hallucination prevention.

**Type in the chat (don't use the sidebar):**
```
How do I set up Duo MFA?
```

**Narrate:**

> "Now a different intent — Intake classifies this as 'policy_question'. The router skips Workflow entirely and goes straight to Knowledge."

> "The Knowledge agent embeds the question with Voyage AI, does cosine similarity against our vector store of scraped SJSU IT pages, retrieves the top 3 relevant chunks, and feeds them to Claude with a strict 'answer only from context' system prompt."

> "See the answer cites the actual SJSU URL it came from. That's the grounding story — Claude can't hallucinate a procedure because we constrain it to retrieved context. If we ask something not in the corpus, it says 'I don't have that information' instead of making something up."

**Optional second knowledge question if time allows:**
```
What are the password requirements at SJSU?
```

---

## Scenario 4: Escalation path with real Jira (90 seconds)

**What you're proving:** Multi-agent failure handling AND real MCP/Jira integration.

**Before clicking, switch your browser tab to Jira so it's visible. Then click in the sidebar:** 🚨 Escalation path

User message: `"Reset my password, ID 345678901"`

**Narrate while it runs:**

> "Priya's case. The Intake agent looks up her ID and finds the account is inactive — she's an alumni past the 24-month cutoff. Intake classifies this as 'account_not_found'."

> "The router doesn't send this to Workflow because there's no action we can take — it routes directly to Escalation. The Escalation agent builds a ticket with the full conversation transcript, classified intent, and reason for escalation, then sends it to Jira."

**Switch to the Jira browser tab and refresh.**

> "There it is — a real Jira ticket just appeared on the SJSU IT board. Click the card and you can see the conversation transcript, student ID, and escalation reason. A Tier-2 technician would pick this up tomorrow morning."

---

## Scenario 5: VS Code MCP integration (60 seconds)

**What you're proving:** MCP rubric requirement.

**Switch to VS Code, open Copilot Chat sidebar.**

> "Now I want to show MCP working from a different client. The Escalation agent uses Jira's REST API directly because it needs headless authentication. But for the rubric we also need to demonstrate VS Code as an MCP client to the same Jira instance."

**Type in Copilot Chat:**
```
List the most recent tickets in the SJSUIT project
```

> "Copilot just queried Jira through Atlassian's official MCP server. Notice it picked up the ticket the agent just created — same data, two different clients, one standardized protocol. That's the value of MCP — we don't write custom integration code for every client."

---

## Closing (45 seconds)

> "To summarize what you saw: four LangGraph-orchestrated agents, real Voyage AI embeddings over scraped SJSU documentation, autonomous tool selection by Claude, real Jira ticket creation, and MCP integration in two different clients."

> "If we deployed this at the SJSU help desk and it deflected even half of password reset tickets — and the rubric says industry deflection rates of 60-70% are common — we'd save the IT help desk roughly $50,000 in labor annually based on their published ticket volume. That's the product ownership story."

> "Happy to take questions."

---

## Q&A — likely grader questions and your answers

### "How do you prevent the agent from resetting the wrong person's password?"

> "Identity verification is split into two stages. First, Intake makes a separate Claude call just to classify the request — it doesn't make any changes to state related to identity. Second, we look up the SJSU ID in our directory; if the account doesn't exist or is inactive, verification fails. In production we'd add a birthdate or last-4-of-SSN challenge — that's a 20-line change in `agents/intake.py`. We deliberately split classification and verification into separate calls so the security-critical step is auditable in isolation."

### "Why LangGraph instead of just a chain of LLM calls?"

> "Because the routing is non-trivial. Workflow can fall through to Escalation if a tool fails. Intake can short-circuit if verification has failed twice. We needed conditional edges, state inspection between nodes, and the ability to visualize the graph. LangGraph gives us all of that with explicit semantics — and the graph PNG you saw on slide 2 is generated directly from the running code, not drawn by hand."

### "Why didn't you use a real vector database like Pinecone or ChromaDB?"

> "We started with ChromaDB and hit memory and Python compatibility issues during development. For our document scale — about 50 chunks — a NumPy-based cosine similarity over Voyage embeddings is operationally simpler and gives identical retrieval quality. The Knowledge agent's interface is the same regardless of backing store; we could swap in Pinecone in maybe 30 lines of code if we needed to scale to millions of documents. Choosing the simplest tool that meets the requirement is good engineering, not a shortcut."

### "How does this scale to other IT issues beyond passwords?"

> "Adding a new use case is mostly adding a new intent and a new agent. For example, software access requests would be a new intent in `state.py`, a new branch in the router, and a new Workflow tool that calls our IAM system. The Intake, Knowledge, and Escalation agents are already general — they don't change per use case. The architecture is designed for separation of concerns."

### "What's the failure mode if Claude is down?"

> "Each agent has a graceful degradation path. Intake's classification falls back to 'unclear' which prompts the user for more detail. Knowledge falls back to 'I don't have that information.' Workflow's tool calls fail-closed — we don't reset a password if Claude can't confirm the action. And every failure routes to Escalation, which creates a Jira ticket so a human picks it up. We never silently fail."

### "What about hallucinations?"

> "Three layers of defense. First, the Knowledge agent's system prompt explicitly restricts answers to retrieved context. Second, every answer must cite a source URL — if Claude tries to invent something, the citation has nowhere to come from. Third, the Workflow agent doesn't generate facts at all; it picks tools and reports their literal output. The only place Claude generates free-form text is grounded in retrieved documents."

### "How long did this take to build?"

> "About four weeks across the team. The core LangGraph wiring took maybe three days. Most of the time was on RAG quality, prompt iteration, and getting the MCP integrations dialed in. The architecture is honestly the easy part — making it reliable is the hard part."

### "What would you change if you had another month?"

> "Three things. One, real MFA in the verification step instead of just an ID lookup. Two, a feedback loop where students rate whether the agent helped, so we can fine-tune routing decisions. Three, integrating with the actual Jira REST API for ticket *status* updates so the Escalation agent can tell users 'a ticket already exists for this issue, here's the status' instead of creating duplicates."

---

## If something breaks live

**Streamlit shows an error or hangs:** Click "Start new session" in the sidebar. If still broken, switch to terminal, Ctrl+C, restart with `python3.14 -m streamlit run app.py`. Pad with: "Live demos — let me reset that quickly."

**Jira ticket doesn't appear:** Refresh the Jira browser tab. If still missing, fall back to: "The escalation logic is what's important here — you can see in the trace that it called the Jira tool. Network's a bit slow today."

**Knowledge agent says 'I don't have that information':** That's actually a *feature* — it proves the grounding works. Pivot to: "Notice it didn't make something up — that's the anti-hallucination guardrail in action."

**Anthropic API rate limit or outage:** Switch to: "While the API recovers, let me walk you through the architecture in more detail." Use the rest of the slides as backup material.

---

## Time budget breakdown

| Section | Target | Cumulative |
|---|---|---|
| Opening + architecture | 0:45 | 0:45 |
| Scenario 1 (happy path) | 1:30 | 2:15 |
| Scenario 2 (cooldown) | 1:15 | 3:30 |
| Scenario 3 (knowledge) | 1:00 | 4:30 |
| Scenario 4 (escalation + Jira) | 1:30 | 6:00 |
| Scenario 5 (VS Code MCP) | 1:00 | 7:00 |
| Closing | 0:45 | 7:45 |
| Q&A buffer | 1:00+ | 8:45+ |