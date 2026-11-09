# ADR-0033 — An SME answer to unchanged text carries over an addendum, and audit rows fit Teams conversation IDs

- **Status:** Accepted
- **Date:** 2026-11-02
- **Deciders:** Solution architecture proposed the design; the user asked for both fixes before a pilot
- **Related:** [ADR-0010](0010-addendum-reconciliation-as-immutable-revisions.md), [ADR-0030](0030-rfp-orchestrator-and-hooks.md), [ADR-0032](0032-rerun-safe-package-facts-and-proposals.md)

## Context

An RFP D run in test with real Teams cards (`eval/reports/20261102-033426-D-test.md`) and the first Orchestrator test in
Teams found two faults.

1. **An addendum after gaps were answered repeated the work and raised false flags.** Stage 3 deletes the gaps, so each
   requirement was routed again and the SME got a new card, even when the addendum hadn't touched it. The earlier answers
   stayed as Draft `PROP-<key>-<requirement>` entries, which stage 5 then flagged as LIBRARY-DRAFT-PRESENT. All 5 false flags
   in that run cite these entries, and flag precision was 0.80 against a threshold of 0.85.
2. **Audit rows weren't written for Teams conversations.** `RFP - Hook Post Tool Use` ran and failed: a Teams conversation ID
   was 131 characters and `hrs_correlationid` holds 100. Hooks fail open ([Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agents-experience/hooks-overview)),
   so the agent carried on and nothing looked wrong. The Copilot Studio test chat's IDs are short, so earlier drills passed.

## Options considered

For the repeated card:

- **Skip stage 3's gap deletion.** Stages 4-9 rebuild from a clean state (ADR-0010 amendment 1), and keeping rows would break that.
- **Compare against the addendum's change list.** This depends on stage 3's output being complete, and a rerun would need it too.
- **Compare the entry's saved question with the current requirement text (chosen).** It is exact and deterministic, it needs
  no new column, and it works for any way back into stage 7.

For the flags:

- **Change the answer key or the threshold.** This hides the behavior rather than fixing it.
- **Leave this RFP's own Drafts out of stage 5's library read (chosen).** Hard rule 5 is unaffected: stage 5 reads fewer unusable
  entries, and Drafts from other RFPs still flag.

For the audit write:

- **Widen the column.** This is a schema change for a value the audit text already holds in full.
- **Cut the value to 100 characters (chosen).**

## Decision

- Stage 7 reads this RFP's Draft proposals. If one's question is exactly the current requirement text and its answer isn't
  blank, the gap is written as Answered with that text and linked to the entry. No card goes out, and the run doesn't wait for
  it. Any change to the text, even punctuation, asks the SME again. The NO-LIBRARY-ANSWER flag says the answer was reused.
- `RFP - Route Gap To SME` starts only for a gap created as Routed.
- Stage 5 leaves out Draft entries named `PROP-<this RFP's key>-*`. An entry that has left Draft is read as usual.
- Both hook flows write the first 100 characters of the conversation ID to `hrs_correlationid`. The audit text keeps the full ID.

## Consequences

- An SME who wanted to change an answer after an addendum gets no card if the text is unchanged. They can still edit the
  Draft entry, and a rerun from a stage before the answer removes it and asks again.
- The approval reviewer sees the reused answer as usual, and the flag detail says where it came from.
- Rows written before this change keep their old flag wording.
- A hook failure still stays silent. If an audit row is missing, check the run history of `RFP - Hook Post Tool Use`.

## Results

- RFP D with the addendum during the run: 5 answers carried over, only the new THPA-051 got a card, and flag precision was
  0.923 in dev and test (`eval/reports/20261102-130253-D-dev.md`, `20261102-151351-D-test.md`).
- In Teams, a denied rerun wrote its audit row with a 100-character ID.
- After the fix, two status questions in the Teams chat started no Post tool use run, though the same question did in the
  test chat. The cause is **[UNVERIFIED]**; ADR-0034 removes the dependency. After the ADR-0034 import, the hook fired in that
  chat and wrote rows with a 100-character ID, which shows the overflow fix for a successful call.
