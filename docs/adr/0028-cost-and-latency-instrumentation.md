# ADR-0028 — Each stage records its prompt credits and machine time; the cost estimate is re-based on prompts

- **Status:** Accepted
- **Date:** 2026-10-31
- **Deciders:** Solution architecture, with the user's approval in the Slice 7 plan
- **Related:** [ADR-0001](0001-deterministic-workflow-spine-agents-at-the-leaves.md), [ADR-0009](0009-evaluation-harness-outside-the-platform.md), [ADR-0015](0015-leaf-steps-are-prompts-called-by-agent-flows.md), [ADR-0021](0021-self-consistency-for-judgment-prompts.md), ARCHITECTURE.md §14.2 and §14.3, risk R5

## Context

Slice 7's exit asks that credit use stay within the estimate. Through Slice 5 nothing was measured:

- `hrs_runlog.hrs_creditsestimate` existed but no flow wrote it.
- Every eval report said "not instrumented".
- No actual figure was recorded for any slice beyond the M0 spike (3 credits, 1,586 tokens for one prompt call,
  ADR-0015).

§14.3's estimate (~1,050 credits for 110 requirements) still priced agent actions and generative answers, but
ADR-0015 replaced the agents with prompts called from flows. ADR-0021 then ran the judgment prompts two or three
times each.

**Platform facts (checked 2026-10-31):**
- Prompts in agent flows *"always consume Copilot Credits"*, billed by tokens: 0.1, 1.5 or 10 credits per
  1K tokens for basic, standard or premium models.
- Agent-flow actions cost 13 credits per 100.
- The Power Platform admin center offers daily downloads per environment, agent and feature. No consumption API is
  documented.

Sources: [billing rates](https://learn.microsoft.com/en-us/microsoft-copilot-studio/requirements-messages-management),
[AI Builder in agent flows](https://learn.microsoft.com/en-us/ai-builder/message-management),
[capacity reports](https://learn.microsoft.com/en-us/power-platform/admin/manage-copilot-studio-copilot-credits-capacity).

Spike M0.4 confirmed that a prompt action's output in a flow carries `costAsCopilotCredits` and `totalTokens`. For
example, one RFP Judge Relations call: 9 credits, 5,088 tokens. That is the standard rate.

## Decision

**Prompt credits, recorded by the flow.**
- Each prompt action gets a sibling action that appends its `costAsCopilotCredits` to a run variable.
- Anything that ran after the prompt also waits for that append (`instrument_prompts` in `tools/flowgen/build.py`).
- Each stage resets the variable when it starts. When it ends, it writes the sum to its run-log row's
  `hrs_creditsestimate`. Expressions have no sum, so it is taken with XPath over the array as XML.
- The stage-2 and stage-7 waits, and gate failures, write the sum too.

**Machine time**
- Machine time is each run-log row's completion minus its start, summed per stage.
- Human waits fall between rows, so they are excluded.
- Targets are §14.2's RFP A column (`eval/thresholds.json` `latency_minutes`). As built, stage 6 also drafts, so
  §14.2's 15 minutes for select-and-draft and 5 for policy are split 10 and 10. Latency is reported, not scored.

**Flow-action credits, estimated by the harness.**
- `eval/rfp_eval/flowruns.py` counts executed actions in the run history and adds each loop's inner actions once
  per repetition. Repetition counts come from the history, inner sizes from the flow definition. It charges 0.13
  per action.
- Skipped actions and repetitions aren't counted. Reconciled on 2026-11-03, it matches billing within 1% (below).
- The admin center's daily download stays the authoritative figure. `docs/RUNBOOK.md` reconciles one day against it.

**Report.** Every eval report gets a Cost and latency section:
- credits and minutes per stage, against the targets
- the prompt total, the flow-action estimate, the grand total, and the credit budget

`eval/run.ps1` passes the run's start time (`--since`), so retries and RFP D's addendum reprocess count in full and
earlier passes don't. `rfp_eval.cli usage` writes the same section for a run with no answer key (SCALE-500,
ADR-0029).

**Re-based estimate (written before the exit run).** Per-call figures are from the Slice 5 runs' history; all
calls run at the standard rate.

| Stage | What runs (RFP A, 60 requirements) | Prompt credits |
|---|---|---|
| 1 | Extract RFP Facts, once | ~15 |
| 2 | Classify, once (B: Identify ×3 as well, ~36) | ~9 |
| 4 | Judge Relations ×3 | 27 (measured) |
| 5 | Shortlist in 3 batches, Select ×2 in 3 batches | 123 (measured) |
| 6 | Screen Rules, Adjudicate, Draft in 5 batches | 79.5 (measured) |
| 7 | Draft Clarifications | 4.5 (measured) |
| 8 | Draft Section ×10 | 138 (measured) |
| | **Prompts** | **~400** |
| | Agent-flow actions, about 600 at 0.13 (block and requirement writes in stages 1-2 add ~300 to the 295 measured for 4-9) | ~80 |
| | **RFP A** | **~480** |

- **RFP B:** 90 requirements, plus Identify. About 650.
- **RFP D:** runs stages 3-9 twice, base and addendum reprocess. About 850.
- **SCALE-500:** stages 5, 6 and 8's per-requirement prompts scale linearly, and stage 8's sections are capped, so
  roughly 2,500-3,000.
- Document Intelligence pages are Azure costs, not Copilot credits.

**Budgets** (`eval/thresholds.json` `credit_budget`, checked in every report, warning not failing):
- **A and B:** 1,000 each, §14.3's order of magnitude per RFP.
- **D:** 1,500, because of the second pass.

## Results (Slice 7 exit run, dev, 2026-11-01)

From each report's Cost and latency section. These are the harness's figures; the reconciliation below compares them
with the admin center.

| RFP | Result | Machine minutes | Prompt credits | Agent-flow actions | Total credits | Estimate | Budget |
|---|---|---|---|---|---|---|---|
| A | PASS | 4.0 | 394.5 | 119.7 | 514.2 | ~480 | 1,000 |
| B | PASS | 4.5 | 576.0 | 134.8 | 710.8 | ~650 | 1,000 |
| D | PASS | 8.8 | 726.0 | 190.3 | 916.3 | ~850 | 1,500 |

Each RFP ran 7-9% over its estimate and well inside its budget. Agent-flow actions cost more than estimated (A: 120
against ~80). Reports: `eval/reports/20261101-021819-A-dev.md`, `20261101-023224-B-dev.md`, `20261101-025517-D-dev.md`.

The test acceptance run agreed: A 514.4, B 707.7, D 914.7 (ADR-0031).

**Re-based estimate (2026-11-01, user's decision).** The Slice 7 criterion "within the estimate" is met by staying
within budget. The estimates become the measured figures: **A 515, B 710, D 915**. The budgets don't change. The
earlier estimates stay above for the record.

## Reconciliation against the admin center (2026-11-02)

The Copilot Credits report was downloaded by script on the evening of 2026-11-02, with a 30-day look-back. It was compared with
every Run Pipeline run in each environment's flow history, because the file can't isolate one day (see below).

**Corrected 2026-11-03.** The first comparison found two gaps over 10%, and both were errors in the comparison:
- It reported the flow-action estimate as 49.8% high. The file's flow-action figures stop at the end of 2026-11-01 UTC, but its
  prompt figures run into 2026-11-02, and the comparison counted runs from both days.
- It reported dev's prompts as 13.1% short. The older runs were read by action name and only for succeeded actions, which
  missed prompt calls.

The comparison is now `rfp_eval.cli reconcile` (docs/RUNBOOK.md §7). It counts every executed action, repetitions included, and reads
`costAsCopilotCredits` from every prompt call, recognized by its operation.

| Environment | Figure | Admin center | Run history | Gap |
|---|---|---|---|---|
| Test | Prompts | 4,152.0 | 4,152.0 (29 runs, 342 calls) | **0.0%** |
| Test | Run Pipeline actions, runs to end of 2026-11-01 UTC | 730.08 | 724.23 (5,571 actions, 19 runs) | **-0.8%** |
| Dev | Prompts | 34,594.5 | 31,861.5 (183 runs, 2,442 calls) | **-7.9%** |
| Dev | Run Pipeline actions, runs to end of 2026-11-01 UTC | 7,530.77 | 7,479.55 (57,535 actions) | **-0.7%** |

Test's figures come from the command (`eval/reports/20261103-123816-test-reconcile.md`). Dev's come from a scan with the same logic, run
before the command was written. Dev takes about an hour.

- **The harness's flow-action method is right.** Its estimate for a run matches what is billed within 1%. The "upper estimate"
  caveat is dropped.
- **Dev's 2,733 prompt credits (7.9%) aren't in the pipeline's run history.** Every call in it succeeded with no retries, and test
  matched exactly. No code in the repository calls prompts directly. The source is unknown. It's under the 10% threshold.
- Learn says "all actions in the agent flow" are billed, which matches counting every executed action.

**What the report showed about itself:**
- **Pipeline credits appear as non-billed.** Every prompt and flow action row has 0 billed credits. Only the Orchestrator's "Process Agent"
  rows are billed (dev 325.93, test 153.26). The admin center's consumed capacity was 469.18, in line with the billed rows and not the tens of thousands of non-billed credits. Why they are non-billed is
  **[UNVERIFIED]**, and the user-level file lists the admin account as having no Microsoft 365 Copilot license. Don't assume a production
  tenant treats them the same way. Budgets here are compared with billed plus non-billed.
- **The harness doesn't cover the Orchestrator.** Its Process Agent credits are real and are in no eval report.
- **The agent-level file has no date column.** The environment-level file has dates but held only billed credits, and
  only through Nov 1 when this was run. So one day's pipeline credits can't be read.
- **Its two meters had different cutoffs.** Prompt credits covered test runs through 2026-11-02 15:04 UTC, and flow actions stopped
  at the end of 2026-11-01 UTC. Compare actions only up to the last full day.

## Consequences

- Every run is costed per stage without opening the admin center. Risk R5 (cost discourages evaluation) now has
  the instrumentation its mitigation named.
- The prompt instrumentation adds 21 actions to the pipeline, plus one per prompt call at runtime: about 1 credit
  per run.
- §14.3's agent-era table is superseded by this estimate. ARCHITECTURE.md §14.3 points here.
- Microsoft Learn says the admin center's data is aggregated daily and can be delayed, with no refresh schedule. The runbook
  reconciles a day that ended at least 24 hours ago.
