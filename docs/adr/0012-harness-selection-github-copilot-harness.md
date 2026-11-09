# ADR-0012 — Harness selection: GitHub Copilot harness

- **Status:** Superseded for the pipeline steps by [ADR-0015](0015-leaf-steps-are-prompts-called-by-agent-flows.md) (2026-10-28). Still the reference for the `RFP Orchestrator` harness decision in Slice 7.
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0001](0001-deterministic-workflow-spine-agents-at-the-leaves.md), [ADR-0008](0008-proposal-assembly-word-template-content-controls.md)

## Context

Copilot Studio runs everything on a *harness* — the runtime between the author's design and the
model. The harness determines which features are available and how usage is billed, so it has to be
chosen before any agent is built.

Three exist:

| Harness | Built for | Notable capabilities | Billing |
|---|---|---|---|
| **GitHub Copilot harness** | Reasoning-heavy, multi-step business processes | Takes a goal and breaks it into steps; tools across connectors, knowledge, **MCP** and **connected agents**; **skills** and **memory**; natively creates and edits Word, Excel, PowerPoint and PDF files; secure sandbox; **hooks** (preview) | Copilot Credits, usage-based |
| **Customer Conversation harness** | Customer-facing voice and chat, contact-center style | Multi-agent with sub-agents and escalation paths to humans; tools, MCP, connected agents, skills, memory | Copilot Credits |
| **Standard harness** | Rule-based agents and structured conversations | Topics, prompts and defined paths; existing prompt library and enterprise knowledge | Standard harness licensing |

Note the naming caveat Microsoft states explicitly: the GitHub Copilot harness is a Copilot Studio
authoring and orchestration framework, **not** the GitHub Copilot service. Customer data is not sent
to or processed by GitHub Copilot; Copilot Studio's privacy, security, compliance and data-residency
commitments continue to apply. That distinction matters for the security review of a system handling
buyer solicitations.

What this solution's agents actually need:

- **MCP tools** — the five specialists call `rfp-tools` operations;
- **Hooks** — audit, redaction and the deny check in §10.2 of the architecture;
- **Connected agents** — available if the Orchestrator ever needs to delegate, though the pipeline
  does not depend on it ([ADR-0001](0001-deterministic-workflow-spine-agents-at-the-leaves.md));
- **Reasoning** — for equivalence judgments and policy adjudication;
- **Document reading** — the Extraction agent reads normalized blocks, but native file handling is
  useful for working artifacts.

What they do **not** need: voice, phone numbers, escalation to a human representative, website chat,
or a topic-and-path conversation design.

## Options considered

### Option A — Standard harness

- **For:** Predictable, rule-based behavior, which sounds like a good match for a compliance use
  case. Established licensing. The VS Code clone layout is documented against it.
- **Against:** Its predictability is the wrong kind. The determinism this solution needs is in the
  *pipeline*, and that is already supplied by the workflow spine. What the agents need is exactly
  what the standard harness does not focus on: MCP tools, hooks, reasoning, skills and memory. Its
  documentation lists files and skills/memory as "not a focus".

### Option B — Customer Conversation harness

- **For:** Multi-agent architecture with sub-agents; supports tools, MCP, connected agents, skills
  and memory.
- **Against:** Built for customers over voice and chat, with escalation to human representatives.
  None of that applies. Its sub-agent escalation model is a poor fit for internal pipeline steps.

### Option C — GitHub Copilot harness *(chosen)*

- **For:** The only harness that supplies everything the specialists need — MCP tools, hooks,
  connected agents, reasoning, skills, memory and native file handling — and Microsoft names it the
  best fit for exactly this shape of work ("an accounts payable process, where an agent or workflow
  reads invoices, matches them to purchase orders, and routes exceptions for approval", which is
  structurally the same problem as matching requirements to answers and routing exceptions).
- **Against:** Usage-based Copilot Credit billing, including during building, testing and
  evaluation, which makes repeated evaluation runs a real cost. Hooks are preview on this harness.
  Some documentation — notably the VS Code clone layout — is written against the standard harness,
  so ALM details need verification here.

## Decision

**Option C — the GitHub Copilot harness** for all six agents.

## Consequences

Accepted deliberately:

- **Billing is usage-based**, and building, testing and evaluating consume Copilot Credits. The
  architecture's cost section budgets roughly 1,000 credits per RFP and ~4,000 per full four-RFP
  evaluation run, which is why development runs are single-RFP.
- **Reasoning models are enabled per step, not globally.** A reasoning model adds the premium rate
  (10 credits per 1,000 tokens) on top of the feature rate, so reasoning is on for equivalence
  judgments and policy adjudication only. Drafting uses a standard model.
- **Hooks are preview.** No control that must hold depends on them, because hooks fail open by
  design (see the architecture's §10.2). This is also open question Q2 — whether preview features
  are acceptable in the pilot at all. If the answer is no, the hooks' audit duties move into the
  stage flows and nothing else changes.
- **Tenant graph grounding is off.** It costs 10 credits per message and this solution grounds on
  specific, curated sources rather than the tenant graph. Leaving it on would add cost and widen the
  grounding surface in a system whose whole point is controlled sourcing.
- **The standard harness remains an option for `RFP Orchestrator` alone** if its conversational
  needs turn out to be narrow and its credit consumption disproportionate. It is the one agent with
  no MCP, reasoning or file requirement. Deferred to slice 7 rather than decided now.

### Verification owed

- **[UNVERIFIED]** Whether agents on this harness export and unpack into the `bots/` +
  `botcomponents/` layout in [ADR-0011](0011-solution-source-layout-and-regeneration.md). The
  gallery's `blastbox-omega` submission uses connected agents and skills in that layout, which is
  good evidence, but it is not stated as harness-specific. Tested in slice 0.
- **[UNVERIFIED]** Whether Microsoft 365 Copilot license inclusions apply to the bid team's usage.
  Agent actions and agent flow actions invoked by a Microsoft 365 Copilot-licensed user through the
  *When an agent calls the flow* trigger are included at no charge, which would materially change
  the cost model for interactive use of the Orchestrator. Pipeline runs are triggered by a service
  identity, so the inclusion is unlikely to apply to them.
