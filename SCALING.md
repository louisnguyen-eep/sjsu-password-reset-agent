# Scaling Plan

How this system grows from a capstone prototype serving five fake SJSU students to a production deployment serving the full California State University system — 23 campuses, ~460,000 students, ~50,000 faculty and staff.

> Companion to `README.md` and `IMPLEMENTATION.md`. The README explains what we built; IMPLEMENTATION.md explains how; this document explains what comes next.

---

## Table of contents

1. [The case for system-wide deployment](#1-the-case-for-system-wide-deployment)
2. [Multi-tenant architecture](#2-multi-tenant-architecture)
3. [Infrastructure and hosting](#3-infrastructure-and-hosting)
4. [Identity and authentication at scale](#4-identity-and-authentication-at-scale)
5. [Knowledge base operations](#5-knowledge-base-operations)
6. [Security and compliance](#6-security-and-compliance)
7. [Monitoring and observability](#7-monitoring-and-observability)
8. [Cost model](#8-cost-model)
9. [Rollout phases](#9-rollout-phases)
10. [Competitive positioning](#10-competitive-positioning)
11. [Open questions and risks](#11-open-questions-and-risks)

---

## 1. The case for system-wide deployment

The 23 CSU campuses collectively serve more students than any other public university system in the United States. Each campus runs its own IT help desk, with its own ticket queue, hours, and staffing levels — but the underlying problem is identical at every one. Password resets dominate ticket volume across the system.

### Why this matters at the system level

The 23-campus structure creates a unique opportunity. Each campus solves the same problem independently today, duplicating effort. A shared system-wide tool would:

- **Eliminate duplicated engineering.** Today, smaller campuses (Maritime Academy, Channel Islands) can't afford enterprise IT vendors that larger campuses (SJSU, Long Beach, Northridge) can. A shared system levels access.
- **Standardize the student experience.** A student transferring from SJSU to San Diego State today encounters two different password reset flows. Standardization improves transfer-student outcomes.
- **Capture economies of scale.** LLM API costs, vector storage, and Jira licensing all benefit from bulk pricing. One CSU-wide deployment is cheaper per student than 23 independent deployments.
- **Pool training data.** Models improve when they see more conversations. A shared knowledge base benefits from corrections made at any campus.

### What stays campus-specific

Not everything centralizes. The following must remain per-campus:

- The password policies (one CSU campus enforces 16-char minimums, another 12)
- The recovery channels (some use SMS, some email-only)
- The escalation queue (each campus has its own Tier-2 team in its own ticketing system)
- The knowledge base content (every campus has its own IT documentation site)
- The student directory (each campus owns its own SSO infrastructure)

The architecture must respect this — shared brains, isolated data.

---

## 2. Multi-tenant architecture

The capstone prototype is single-tenant: one Streamlit app, one mock directory, one Jira workspace. Scaling to 23 campuses requires a multi-tenant design where every request is tagged with a campus identifier and routed to that campus's isolated resources.

### Tenant boundary

A **tenant** in this design is one CSU campus. Every piece of state — directory rows, RAG corpus, ticketing destination, password policy — belongs to exactly one tenant. No cross-tenant data access is permitted at any level.

### Request flow at scale

```
Student → CSU SSO portal (campus selection)
       → API gateway (tenant header injected from SSO claim)
       → LangGraph orchestrator (loads tenant config)
       → Agents (use tenant-scoped tools)
       → Tenant-specific responses
```

The orchestrator reads a `tenant_id` from the request and passes it through state. Every tool call (`lookup_student`, `reset_password`, `create_ticket`, retrieval) takes the tenant_id and operates only on that tenant's resources.

### Tenant configuration

Each campus has a config record in a central PostgreSQL database:

```python
{
    "tenant_id": "sjsu",
    "display_name": "San José State University",
    "directory_endpoint": "ldaps://ad.sjsu.edu",
    "directory_service_account": "vault://sjsu/ad-svc",
    "password_policy": {
        "min_length": 12,
        "require_complexity": True,
        "cooldown_minutes": 21,
        "alumni_cutoff_months": 24,
    },
    "rag_corpus_id": "sjsu-it-docs-v3",
    "ticketing": {
        "system": "jira_cloud",
        "url": "https://sjsu.atlassian.net",
        "project_key": "SJSUIT",
        "credentials": "vault://sjsu/jira",
    },
    "branding": {
        "logo_url": "...",
        "primary_color": "#0055A2",
    },
}
```

The orchestrator loads this once per tenant per worker and caches it. Configuration changes propagate via a Redis pub/sub channel — no restart required.

### Why not run 23 separate deployments

A simpler approach is to spin up 23 copies of the prototype, each running independently on each campus's infrastructure. This is appealing because it requires zero new architecture. We reject it for three reasons:

**1. Cost.** Twenty-three sets of LLM API contracts, vector DB instances, monitoring tools, and CI/CD pipelines. The duplication tax is substantial.

**2. Drift.** Without a shared codebase deployment, the 23 instances drift over time as each campus's IT team patches and customizes independently. Bug fixes at one campus don't propagate to others.

**3. Cross-campus features.** A unified deployment can offer features that a federated one cannot — transfer-student account migration, system-wide analytics for the Chancellor's Office, shared learnings from prompt iteration.

The trade-off is operational complexity (one team must support 23 tenants), but the gains outweigh the costs at this scale.

---

## 3. Infrastructure and hosting

The prototype runs on a developer laptop. Production needs hosted infrastructure that can serve concurrent load across all CSU campuses during peak hours (typically the first week of each semester).

### Target deployment

**Compute:** A Kubernetes cluster on AWS EKS in `us-west-2` (close to California for latency). Two worker pools — a baseline pool of 3 nodes for steady-state load and an autoscaling pool that grows to 15+ nodes during peak weeks.

**API gateway:** AWS API Gateway in front of the orchestrator service. Handles TLS termination, rate limiting (per-tenant), and routing.

**Orchestrator service:** A FastAPI app running the LangGraph state machine. Stateless — every request carries its own tenant_id and conversation state. Replaces the Streamlit prototype for production traffic; Streamlit remains as an internal admin tool.

**Frontend:** A React SPA hosted on CloudFront. Each campus gets a branded subdomain (`reset.sjsu.edu`, `reset.calstate.edu/csulb`, etc.) that all hit the same API.

**Vector storage:** Pinecone in production — replacing the NumPy/JSON file approach from the prototype. Pinecone offers per-namespace isolation, which maps cleanly onto our per-tenant requirement. Each campus is a namespace.

**Relational storage:** PostgreSQL on AWS RDS Multi-AZ for tenant config, audit logs, and session metadata. Read replicas in two AZs.

**Caching:** Redis (ElastiCache) for tenant config lookups and rate-limit counters.

**Secrets:** AWS Secrets Manager for API tokens, with per-tenant secret paths to enforce isolation.

### Why AWS over Azure or GCP

CSU systemwide IT already has an AWS contract through the GovCloud framework. Sticking with AWS avoids new procurement cycles. If the CSU IT council prefers Azure for compliance reasons (FedRAMP coverage), the architecture transfers cleanly — Azure has equivalent services for every component listed above.

### Capacity planning

Steady-state traffic estimate, based on industry password reset volumes:

- 460,000 students × ~3 password events per year = 1.38M events annually
- 50,000 staff × ~2 password events per year = 100,000 events annually
- Total: ~1.5M conversations per year
- ~4,100 conversations per day average
- ~12,000 per day at peak (first week of fall semester)
- ~30 conversations per minute at peak

Per conversation, roughly:
- 4-6 Claude API calls (Intake classify, Intake verify-context, Workflow tool-pick, Workflow tool-result, Knowledge if hit)
- 1-3 Voyage embedding calls
- 0-1 Jira API call
- 1-2 Pinecone queries

At 30 conversations per minute, that's ~180 LLM calls per minute peak — well within Anthropic's tier-2 rate limits. Pinecone serverless handles this with no provisioning.

---

## 4. Identity and authentication at scale

The capstone uses a SQLite mock directory. Production must integrate with each campus's real identity provider.

### Identity provider landscape across CSU

Every CSU campus runs SSO, but the implementations vary:

- **SAML providers** (most common): Shibboleth (SJSU, Long Beach, Northridge), ADFS, Azure AD
- **OAuth/OIDC**: A few campuses use Okta directly
- **CAS**: Older holdout at some campuses

The orchestrator needs to handle all three protocols. Easiest approach: a per-tenant `identity_adapter` plugin. Each tenant config specifies which adapter to use; the adapter implements a common interface (`verify_session`, `lookup_user`, `reset_password`, `unlock_account`).

### The verification step

The prototype uses a simple ID lookup as verification. Production must do more.

**Tier 1 — passive verification (most users):** The user authenticates to their campus SSO before reaching our agent. The SAML assertion carries verified identity claims (employee ID, email, group memberships). We trust these.

**Tier 2 — fresh challenge (forgot password):** A user who literally can't log in can't pass SSO. For this case we issue a code via the recovery channel registered on the account:
- Primary channel: recovery email registered with the campus (every campus already collects these)
- Secondary channel: SMS to registered phone, if available
- Fallback: security questions (campus-configured)

The code expires in 10 minutes and is single-use. Five failed challenge attempts triggers escalation.

**Tier 3 — escalation:** Privileged accounts (faculty with admin roles, executives, IT staff) cannot self-serve. These users get auto-escalated to their campus's Tier-2 IT support with priority routing.

### Connecting to real AD

Each campus exposes either LDAP/LDAPS to its Active Directory or an Okta-style API. The Workflow agent's tools delegate to a `directory_client` that's resolved from the tenant config.

For LDAPS, we use the `ldap3` library with TLS client certificates issued per-tenant. For Okta-style APIs, we use OAuth client credentials with scopes narrowed to the minimum needed (`users:read`, `users:reset_password`, `users:unlock`).

### Audit trail

Every password reset, unlock, and verification event writes an audit log entry:

```python
{
    "timestamp": "2026-05-14T13:42:18Z",
    "tenant_id": "sjsu",
    "student_id_hash": "sha256-of-id",  # never log raw IDs
    "action": "password_reset",
    "actor": "agent",
    "conversation_id": "uuid",
    "outcome": "success",
    "verification_method": "sms_code",
    "ip_address": "...",
    "user_agent": "...",
}
```

Logs go to a separate, append-only audit store (AWS QLDB or similar) with cryptographic immutability. Required for FERPA compliance and CSU's data governance policies.

---

## 5. Knowledge base operations

The prototype scrapes four SJSU IT pages and embeds them once. Production needs ongoing knowledge base management across 23 campuses with documentation that changes frequently.

### Per-campus corpus

Each campus has its own corpus in Pinecone, namespaced by `tenant_id`. The Knowledge agent queries only the requesting tenant's namespace — never cross-tenant.

### Continuous re-ingestion

IT documentation changes constantly — new MFA tools, updated reset procedures, semester-specific notices. We can't manually re-scrape on a schedule and hope for the best.

The production ingestion pipeline:

1. **Source registry:** A YAML file per tenant listing the URLs (or RSS feeds, or content APIs) to crawl. Tenants can add or remove sources as their IT documentation evolves.

2. **Scheduled crawls:** A Cloud Run job (or AWS EventBridge schedule) runs nightly per tenant. Fetches all registered sources, diffs against the previous crawl, and re-embeds only changed content.

3. **Embedding cache:** Content that hasn't changed since the last crawl reuses its previous embedding. This keeps API costs bounded — we only pay for diffs, not the full corpus.

4. **Version pinning:** Each crawl produces a versioned corpus (`sjsu-it-docs-v47`). The tenant config points at the active version. Rollback is a config update.

5. **Quality checks:** After embedding, a smoke-test query suite runs against the new corpus. If retrieval quality drops below threshold (e.g. expected questions stop returning relevant chunks), the new version is held back from the tenant config update.

### Content access control

Some IT documentation is internal — meant for IT staff, not students. We handle this with metadata tagging:

```python
{
    "content": "...",
    "source": "...",
    "audience": "student",  # or "staff", "internal", "alumni"
}
```

The Knowledge agent passes the requesting user's role to retrieval and filters by audience. A student can't accidentally retrieve content meant for IT admins.

### Cross-tenant learnings (carefully)

Some learnings are generic across all CSU campuses — how MFA works conceptually, what a password manager is. A shared "CSU systemwide" corpus could hold this, and each tenant's queries fall back to it when the campus-specific corpus has no answer.

This is opt-in per tenant. Default-off to preserve isolation guarantees.

---

## 6. Security and compliance

Higher-education IT operates under specific regulatory and policy constraints. Production deployment must address each.

### Regulatory framework

**FERPA (Family Educational Rights and Privacy Act):** Student data — including the fact that a student has an account — is FERPA-protected. We must not log or expose this data in ways that could be accessed by unauthorized parties.

**California Consumer Privacy Act (CCPA):** California-specific privacy law adds disclosure requirements about what data we collect and how it's used.

**CSU Information Security Policies:** The system follows the CSU IS-3 information security framework, which classifies data into Level 1 (public), Level 2 (internal), and Level 3 (confidential). Password reset metadata is Level 3.

### Data handling rules

1. **No raw student IDs in logs.** All audit entries hash the ID with a per-tenant salt.
2. **Conversation transcripts retained for 30 days** then auto-purged. This balances support troubleshooting needs against data minimization.
3. **No conversation data leaves the AWS GovCloud region.** Anthropic's API offers a HIPAA-compliant data residency option that meets this requirement.
4. **PII redaction at the LLM boundary.** Before any user message is sent to Claude, a redaction pass replaces detected SSNs, credit cards, and similar PII with placeholders. The agent operates on placeholders; the original data never leaves our environment.
5. **Zero data retention agreement with Anthropic.** Production uses Anthropic's ZDR (zero data retention) endpoint, which prevents Anthropic from logging or training on our conversations.

### Penetration testing and audit

Before launch, the system needs:

1. **Third-party penetration test.** Cost: ~$25–40K for a comprehensive test, run by a vendor with higher-ed experience.
2. **SOC 2 Type II readiness.** Even if not required, demonstrating SOC 2 controls builds trust with campus CIOs.
3. **Internal red team.** A friendly adversary on the CSU IT team tries to social-engineer the agent — pretending to be other students, trying to extract account info. Findings drive prompt hardening.

### Threat model

We deliberately considered the following attack vectors:

**Account takeover via social engineering:** Attacker tries to convince the agent they're someone else. Mitigation: verification step is non-negotiable; the agent never confirms account details before identity is verified.

**Prompt injection:** Attacker tries to embed instructions in their message to override the agent's behavior. Mitigation: Claude's tool use is constrained to a fixed allowlist; even if the agent "decides" to do something malicious, it can only call the tools we provided.

**Rate-limit-based enumeration:** Attacker enumerates student IDs to learn which ones exist. Mitigation: per-IP and per-session rate limits at the API gateway, combined with consistent response times whether an ID exists or not.

**Data exfiltration via LLM output:** Attacker tries to get the agent to dump its system prompt or retrieved context. Mitigation: system prompt explicitly forbids meta-discussion of the prompt; retrieved context contains only public docs.

---

## 7. Monitoring and observability

A demo that works once is different from a production system that works for years. Observability is what makes the difference.

### Metrics to track

**Operational:**
- Conversation success rate by tenant (target: > 85%)
- Mean time to resolution (target: < 30 seconds)
- Escalation rate (target: < 15%)
- LLM cost per conversation (target: < $0.05)
- API latency p50/p95/p99

**Per-agent:**
- Intake classification accuracy (sampled, human-rated)
- Workflow tool-pick correctness (validated against business rules)
- Knowledge retrieval relevance (sampled)
- Escalation appropriateness (audited weekly)

**Business outcomes:**
- Help desk ticket deflection (% reduction in human-handled password tickets)
- Time-to-resolution improvement vs human baseline
- Student CSAT scores (post-conversation 1–5 rating)
- Cost savings vs. status-quo help desk staffing

### Alerting

PagerDuty integration for:
- Conversation success rate drops below 70% for any tenant (something is broken)
- LLM cost per conversation rises > 3x baseline (prompt injection, malformed inputs)
- Jira API failures > 10% over 15 minutes (integration outage)
- Anthropic API errors > 5% over 5 minutes (vendor outage — switch to a degraded mode)

### Degraded modes

When components fail, we degrade gracefully rather than failing closed:

- **Anthropic down:** Switch to a smaller model (Claude Haiku) automatically; if that also fails, route every conversation directly to Escalation with a "system temporarily unavailable" message.
- **Pinecone down:** Knowledge agent falls back to "I can't access the knowledge base right now — would you like me to escalate to a human?"
- **Jira down:** Escalations queue locally and replay when Jira recovers.
- **Directory down:** Refuse all reset attempts and escalate to "phone the help desk."

Every degraded mode is tested via chaos engineering monthly — we kill components in staging and verify the system stays usable.

---

## 8. Cost model

The capstone runs on free tiers. Production has real costs.

### Per-conversation costs

| Item | Cost per conversation |
|---|---|
| Claude API (4-6 calls, mostly Sonnet-tier) | $0.015 |
| Voyage embeddings (3 calls) | $0.0002 |
| Pinecone queries (3 lookups) | < $0.0001 |
| Compute (orchestrator, RDS, Redis pro-rated) | $0.005 |
| Logging and storage | $0.001 |
| **Total** | **~$0.022 per conversation** |

At 1.5M conversations annually system-wide, that's **~$33,000/year in direct costs.**

### Fixed infrastructure costs

| Item | Annual |
|---|---|
| AWS EKS + supporting services | $120,000 |
| Pinecone (system-wide) | $24,000 |
| Monitoring (Datadog or equivalent) | $36,000 |
| Audit/compliance tooling | $18,000 |
| **Total fixed infra** | **~$200,000/year** |

### Operations costs

| Item | Annual |
|---|---|
| 1 staff engineer (FTE) | $180,000 |
| 0.5 FTE security review | $90,000 |
| 0.25 FTE per-campus liaison × 5 = 1.25 FTE | $225,000 |
| **Total ops** | **~$500,000/year** |

### Total cost of ownership

**~$730,000/year for system-wide deployment** at full scale.

### Comparison to status quo

CSU's combined IT help desk staffing varies, but conservatively:
- ~150 Tier-1 IT support staff across 23 campuses
- Burdened cost of ~$70,000 per FTE = $10.5M/year
- Password resets estimated at 25% of Tier-1 ticket volume = $2.6M/year of Tier-1 effort

If our system deflects 60% of password tickets (industry benchmark for similar AI assistants):
- **Annual savings: ~$1.5M**
- **Annual ROI: ~$770,000** (savings minus our $730K cost)
- **Payback period: ~6 months** from initial deployment

This isn't the only justification — student experience improvements matter independently — but the economic case stands on its own.

---

## 9. Rollout phases

A 23-campus rollout isn't a single launch. It's a sequence.

### Phase 0: Pilot at SJSU (months 1–4)

Single-tenant deployment at SJSU only. Goals:
- Validate the architecture works at production scale (not demo scale)
- Build the multi-tenant abstractions without yet using them
- Train the SJSU IT team on the operational tooling
- Achieve > 80% conversation success rate

Exit criteria: 30 days of stable operation with > 80% success rate and zero security incidents.

### Phase 1: Expand to two reference campuses (months 5–7)

Add CSULB (Long Beach) and Fresno State as the first multi-tenant test. Goals:
- Validate that the tenant isolation actually works
- Surface campus-specific requirements we missed (different SSO, different password policies)
- Build per-campus onboarding playbook

Exit criteria: Three tenants stably running, onboarding playbook documented.

### Phase 2: Larger campuses (months 8–14)

Roll out to the next 7 campuses, prioritized by IT staff readiness:
- Northridge, Pomona, Sacramento, San Diego State, San Francisco State, Fullerton, San Marcos

Cadence: roughly one new campus per month. Each onboarding includes:
- 2 weeks of integration work (SSO, AD, Jira)
- 2 weeks of corpus scraping and validation
- 1 week of internal-only soft launch
- General availability for students

Exit criteria: 10 tenants, system-wide success rate > 85%.

### Phase 3: Remaining campuses (months 15–24)

The remaining 13 campuses, including smaller schools that benefit most:
- Bakersfield, Channel Islands, Chico, Dominguez Hills, East Bay, Humboldt (Cal Poly Humboldt), Maritime, Monterey Bay, San Bernardino, San Luis Obispo (Cal Poly SLO), Sonoma, Stanislaus, plus Cal State LA

Cadence: two campuses per month — smaller campuses are faster to onboard.

Exit criteria: All 23 campuses live.

### Phase 4: Steady state (month 25+)

Ongoing operations, quarterly reviews of:
- Per-tenant success metrics
- Cost per conversation trends
- New use cases beyond password resets (we'd extend the agent design to software access requests, MFA setup, account provisioning for new staff)

---

## 10. Competitive positioning

The capstone brief asked us to position the project as a "lightweight alternative or prototype of enterprise-grade solutions." Here's how we'd actually position this in the market.

### Existing vendors

- **Glean** — enterprise search and AI support. ~$300K/year minimum spend. Strongest at general knowledge retrieval; weaker at workflow automation.
- **Moveworks** — AI IT support platform. ~$200K/year minimum. Strong at workflow automation; expensive and tightly coupled to ServiceNow.
- **Aisera** — similar to Moveworks, slightly cheaper. Common at universities but criticized for opaque pricing and lock-in.

### Our positioning

For a CSU-wide deployment, we don't compete with these vendors on features — we compete on **fit and total cost of ownership.**

A custom-built system tuned to the CSU's specific integrations (SJSUOne, MySJSU, Duo, iSupport, etc.) avoids the integration tax of generic platforms. Our **$730K/year total cost** compares to **$2–4M/year** for a Moveworks or Aisera deployment serving the same population — and we're not exporting CSU student data to a third-party vendor.

The pitch to the CSU Chancellor's Office:

> "We can build and operate a multi-agent IT support system custom-fit to the CSU's existing infrastructure for less than half the cost of commercial alternatives, with full data residency in CSU-owned AWS infrastructure, and we can extend it to use cases beyond password resets without paying additional license fees."

### Why this is genuinely defensible

The architecture isn't trying to be revolutionary. It's an applied integration of well-established components (LangGraph, Claude, RAG, MCP). The value isn't in the AI — it's in the operational fit. Commercial vendors charge a premium for their integration breadth; for a closed system like the CSU, building custom is cheaper and better.

This is the same calculus that drove the CSU to build CalState Apply (the system-wide application portal) rather than license a commercial admissions platform.

---

## 11. Open questions and risks

Honest assessment of what could derail this.

### Technical risks

**LLM reliability.** Claude's tool-use is reliable but not perfect. At 1.5M conversations/year, even a 0.1% failure rate is 1,500 broken conversations. We need to verify that the failure modes are graceful (escalation) rather than catastrophic (wrong password reset).

**Knowledge base drift.** If a campus's IT docs change but our scraping doesn't catch the change, the agent confidently cites outdated information. Mitigation: human review of any high-confidence retrieval that hasn't been verified against fresh content in the past 30 days.

**Vendor lock-in.** Anthropic could change pricing, discontinue Claude, or have an extended outage. Mitigation: the LLM interface is abstracted; we could swap in GPT-4 or Gemini with prompt rewriting in ~2 weeks of work.

### Operational risks

**Per-campus integration variability.** Some CSU campuses' IT systems are 15+ years old. SSO integrations may fail in unexpected ways. Budget for 2x the integration time we estimate.

**IT staff resistance.** Help desk staff may view this as a threat to their jobs. Mitigation: position as augmentation (handles tier-1 password resets, frees staff for higher-value work), not replacement. Involve campus IT teams in the rollout.

**FERPA audit complexity.** Different campuses have different interpretations of FERPA. Some may want stricter logging restrictions than others. The tenant config model accommodates this; the audit process may take longer than expected.

### Business risks

**The Chancellor's Office may not fund this.** The path to system-wide funding goes through the CSU CIO council. They may prefer existing vendor relationships. Mitigation: lead with a Phase 0/1 pilot funded at SJSU level. Success creates pull demand from other campuses.

**Commercial vendors will lobby against this.** Moveworks and Aisera have CSU sales reps. They will frame "build vs. buy" decisions to favor buying. Counter with concrete cost comparisons and the data-residency story.

### Things we don't know yet

- The actual password ticket volumes per campus (we have rough industry estimates, not CSU-specific numbers)
- Each campus's SSO/AD specifics — discoverable only by talking to each campus's IT team
- The political dynamics of getting 23 IT teams to agree on a shared platform
- Whether the Chancellor's Office or individual campuses would own the system

A 6-week feasibility study with the CSU CIO council would answer most of these.

---

## Where this goes

This document is a plan, not a commitment. The next concrete step after the capstone is a 1-page proposal to SJSU's CIO requesting funding for a Phase 0 pilot — a real production deployment serving SJSU students only, validating the architecture at production scale, before any system-wide conversation begins.

If the SJSU pilot works, the conversation with the Chancellor's Office writes itself.
