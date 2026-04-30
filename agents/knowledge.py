"""Knowledge agent — retrieves SJSU IT documentation and answers with grounding.

The core anti-hallucination trick is the system prompt: Claude is told to
answer ONLY from the retrieved context and to say so when context is thin.
"""
from anthropic import Anthropic

from config import CONFIG
from state import AgentState
from knowledge.retriever import retrieve

_client = Anthropic(api_key=CONFIG.anthropic_api_key)


KNOWLEDGE_SYSTEM = """You are the Knowledge agent for SJSU IT password support.

Answer the user's question using ONLY the context below. If the context does
not contain the answer, say "I don't have that information in my SJSU
knowledge base — let me escalate this to a human." Do not guess.

Keep answers short (2-4 sentences). Cite the source URL in parentheses at
the end of any fact you pulled from context.

Context:
{context}
"""


def _format_context(docs: list[dict]) -> str:
    blocks = []
    for i, d in enumerate(docs, 1):
        blocks.append(
            f"[Doc {i}] source: {d['source']}\n{d['content']}"
        )
    return "\n\n".join(blocks) if blocks else "(no relevant docs found)"


def knowledge_node(state: AgentState) -> dict:
    """Retrieve top-k docs, ask Claude to answer grounded in them."""
    last_user_msg = next(
        (m["content"] for m in reversed(state["messages"]) if m["role"] == "user"),
        "",
    )

    docs = retrieve(last_user_msg, k=CONFIG.retrieval_k)

    response = _client.messages.create(
        model=CONFIG.model,
        max_tokens=CONFIG.max_tokens,
        system=KNOWLEDGE_SYSTEM.format(context=_format_context(docs)),
        messages=[{"role": "user", "content": last_user_msg}],
    )
    answer = response.content[0].text.strip()

    return {
        "retrieved_docs": docs,
        "grounded_answer": answer,
        "messages": [{"role": "assistant", "content": answer}],
    }