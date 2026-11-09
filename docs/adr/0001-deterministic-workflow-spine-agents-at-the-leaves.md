# ADR-0001 — Deterministic workflow spine, agents at the leaves

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0006](0006-compliance-value-and-approval-level-are-code.md), [ADR-0012](0012-harness-selection-github-copilot-harness.md)

## Context

The brief's starting hypothesis is hub-and-spoke: an orchestrator **agent** that coordinates seven
specialist **agents** (intake and extraction, addendum reconciliation, answer retrieval and
drafting, policy and compliance, gap routing and clarification, proposal assembly, approval and
filing). The brief asks for this to be challenged rather than copied.

Three facts bear on the choice.

1. **The process is fixed.** The nine stages run in one order with hard dependencies: you cannot
   evaluate policy rules before requirements exist, cannot assemble before drafting, cannot ask for
   approval before counting pages. There is no open-ended planning problem for a model to solve.
2. **Microsoft discourages the pattern for this use case.** The orchestrator/sub-agent guidance
   says not to use it when "the use case requires a consistent success rate (compliance or Q&A use
   cases)" or when "the process includes well-defined steps and strong hierarchical dependencies",
   and describes it as tolerating "spray and pray" approaches "where a single success justifies all
   prior failed attempts". An RFP response factory is a compliance use case with a hard deadline;
   one success out of several attempts is not a useful outcome.
3. **Cost differs by roughly 38×.** At the published rates, an **agent action** — which includes a
   tool call the model chooses to make — is **5 Copilot Credits**, while **agent flow actions** are
   **13 Copilot Credits per 100 actions**, about **0.13 each**. With 311 requirements across the
   four pilot RFPs and several deterministic steps per requirement, putting deterministic work
   behind a model decision is both more expensive and less reliable.

A fourth fact enables the alternative: a workflow can invoke a published agent as a step, using the
**agent node** (`Run an agent`) in the agent-flow designer. The pipeline does not have to be an
agent in order to use agents.

## Options considered

### Option A — Agent orchestrator with seven connected specialist agents (the brief's hypothesis)

The orchestrator agent reasons about which specialist to call next.

- **For:** Modular; each specialist owns its domain; matches the gallery's RFP Response Manager
  shape; easy to extend with a new specialist.
- **Against:** The orchestrator can choose a wrong or out-of-order step, and the failure is silent
  and non-reproducible. Deterministic work (counting, date arithmetic, rule thresholds) becomes
  agent tool calls at 5 credits each. Re-running one requirement means re-running a conversation.
  Microsoft's guidance specifically warns against it here. Gates become instructions rather than
  code.

### Option B — Pure workflow, no agents

Everything in flows, with prompt nodes for text generation.

- **For:** Maximum determinism and the cheapest option; fully reproducible.
- **Against:** Several steps genuinely need language judgment — where a requirement starts and ends
  in prose, whether two differently worded requirements are the same, whether a rule's written
  condition is actually met, how to word a clarification question. Forcing these into prompt nodes
  gives up tool use and grounded knowledge exactly where they help.

### Option C — Deterministic workflow spine calling agents as steps *(chosen)*

A pipeline agent flow runs the nine stages in order, persists state after each, and calls
specialist agents through the agent node for the steps that need judgment. One conversational
agent sits beside the pipeline as the human entry point.

- **For:** The order, the gates, the arithmetic and the mapping are code and cannot drift. The
  model is used only where it adds value. Each stage is idempotent on the requirement natural key,
  so any stage can be re-run. Deterministic steps cost ~0.13 credits instead of 5. Aligns with
  Microsoft's own guidance for compliance use cases.
- **Against:** Less "agentic" than the brief imagined, and adding a stage means editing a flow
  rather than adding an agent. The spine becomes a single component that must stay readable. Some
  reviewers will read a flow-driven design as less sophisticated.

## Decision

**Option C.** A deterministic workflow spine (`RFP - Run Pipeline`) executes the nine stages and
calls five specialist agents as steps. One conversational agent (`RFP Orchestrator`) is the human
entry point and does not drive the pipeline.

Agent / flow / tool / code allocation:

| Brief's candidate | Becomes |
|---|---|
| Intake & Extraction | Flow + `normalize_document` (code) + **RFP Extraction** agent for prose and OCR boundaries |
| Addendum Reconciliation | Flow + `diff_requirement_sets` (code) + **RFP Equivalence** agent for reworded matches |
| Answer Retrieval & Drafting | Flow performs the filtered query; **RFP Drafting** agent writes the text |
| Policy & Compliance Check | `match_rule_candidates` and `evaluate_thresholds` (code) + **RFP Policy Adjudication** agent + `derive_compliance` (code) |
| Gap Routing & Clarification | `route_sme` (code) + **RFP Clarification** agent for question wording |
| Proposal Assembly | Flow + Word Online template population + `count_words` / `count_pages` (code) |
| Approval & Filing | Flow only — no agent |

Stage flows are agent flows. The two flows that wait on a human for hours or days — SME gap
routing and final approval — are Power Automate cloud flows, so they do not hold Copilot Studio
capacity while idle and are not subject to agent flow enforcement.

## Consequences

- Gates G1-G6 are code and cannot be waived by a model.
- Any stage can be re-run for one RFP or one requirement; `hrs_runlog` holds the restart point.
- Estimated consumption of roughly 1,000 Copilot Credits per RFP, of which only ~300 is agent
  actions. Under Option A the agent-action share would be roughly ten times larger.
- Adding a pipeline stage is a flow change plus a `solutioncomponents` change, not a new agent.
- The pipeline is not conversational. Users interact with `RFP Orchestrator`, which needs its own
  read-only tools over the runtime tables.
- If a future requirement genuinely needs open-ended planning — for example, deciding bid strategy
  — that is a separate agent, not a change to this spine.
