"""Central configuration. Import CONFIG from here; do not hardcode values elsewhere."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Claude
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    model: str = "claude-sonnet-4-5"  # good balance of cost + capability for routing
    max_tokens: int = 1024

    # Vector store
    chroma_path: str = "./data/chroma"
    collection_name: str = "sjsu_it_docs"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    retrieval_k: int = 3

    # Mock AD
    students_db_path: str = "./data/students.db"
    cooldown_minutes: int = 21  # SJSU's real lockout cooldown

    # Jira (via MCP)
    jira_project_key: str = "SJSUIT"
    escalation_queue: str = "Tier-2-Password-Support"

    # Behavior
    max_verification_attempts: int = 2


CONFIG = Config()