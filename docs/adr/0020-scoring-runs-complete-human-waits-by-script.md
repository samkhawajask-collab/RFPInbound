# ADR-0020 — Scoring runs complete human waits by script

- **Status:** Accepted
- **Date:** 2026-10-29
- **Deciders:** Solution architecture
- **Related:** [ADR-0009](0009-evaluation-harness-outside-the-platform.md), [ADR-0018](0018-one-pipeline-flow-chained-through-dataverse.md)

## Context

The pipeline stops twice for people: at stage 7 for SME gap answers, and at stage 9 for approval
(ADR-0018). Tuning against the answer key needs many runs, often several an hour. In Slice 1 one full
run waited about 13 hours for 19 Teams cards. A card answered on a superseded run was lost, because
cards from a rerun stage 7 stay live (Q9).

None of the metrics depends on what a person types:
- a gap's compliance is Gap whatever the SME answers;
- gap routing is scored on the SME the gap was routed to;
- page count is measured before approval.

## Options considered

- **A — A person answers every card in every scoring run.** This is the most faithful option, but it
  makes tuning impractical and it puts dozens of cards in front of the pilot recipient.
- **B — Complete the waits by script and pause the card flows during tuning runs** *(chosen)*.
- **C — Score at stage 7.** Stage 8 output would then be unscored: page count and limit breaches.

## Decision

**Option B.**

- **`scripts/complete-gaps.ps1`** closes every open gap of the RFP as *Cannot answer*, cancels their card
  runs and sets the RFP back to Running.
- **Scoring happens at stage 9 *Awaiting human*.** The approval row exists then, with its page count.
  The run is not approved by script.
- **During tuning, `RFP - Route Gap To SME` and `RFP - Request Approval` are stopped in Power Automate.**
  Their Dataverse triggers do not fire, so no cards are posted. They are restarted when tuning ends.
  `scripts/apply-flows.ps1` starts every flow it applies, so the card flows have to be stopped again
  after each flow apply during tuning.
- **`scripts/rewind-run.ps1`** reruns from any stage ≥ 4 by deleting what that stage and later stages
  wrote, and cancels any open card runs. It also deletes the Draft library proposals the rerun's gaps
  created, so those proposals don't pollute `LIBRARY-DRAFT-PRESENT` on the next run.
- **`eval/run.ps1 -FromStage N`** chains them: rewind, wait, complete gaps, wait, score.

## Consequences

- Scored runs don't exercise the Teams cards. The cards were exercised by hand in the Slice 1 run, and
  Slice 7's hardening covers them, including Q9 (withdrawing superseded cards).
- The SME-answer → Draft-library path isn't exercised by scored runs either.
- Every tuning report records the change that prompted it (`--notes`), so the history in
  `eval/reports/` shows how each threshold was reached.
