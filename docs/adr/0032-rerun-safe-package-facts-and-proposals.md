# ADR-0032 — Stage 9 reads saved package facts, and a repeated gap answer reuses its library proposal

- **Status:** Accepted
- **Date:** 2026-11-01
- **Deciders:** Solution architecture proposed the fix; the user told it to go ahead
- **Related:** [ADR-0010](0010-addendum-reconciliation-as-immutable-revisions.md), [ADR-0018](0018-one-pipeline-flow-chained-through-dataverse.md), [ADR-0026](0026-failure-handling-and-stage-reruns.md)

## Context

A run with real Teams cards on RFP D in test found two faults that scripted runs had hidden.

1. **Stage 9 failed whenever it started a new flow run.** Stage 8 computes the file list and page count in a Compose
   action, and stage 9 read that action. The action only has a value when stage 8 ran in the same flow run. A rerun, a
   rewind or the automatic retry starts at stage 9, so the approval row got an empty page count and the write failed.
   RFP B ended up Failed in dev and test this way.
2. **A gap answered again after an addendum stalled the run.** Each answer creates a Draft library entry with the code
   `PROP-<key>-<requirement>`, and that code is unique. An addendum rerun routes the same requirements again and keeps
   the earlier entries, so the create failed with a duplicate key (412). The flow failed before its check that resumes
   the pipeline, and the run stayed at stage 7 with every answer saved. `complete-gaps.ps1` closes gaps without
   creating entries, so evaluation runs never reached this.

## Options considered

For stage 9:

- **Run stage 8 again whenever stage 9 runs.** This needs no new column, but it repeats section writing, spends credits,
  makes new files and can duplicate stage 8's limit-breach flag.
- **Treat a stage 9 rerun as a stage 8 rerun.** This follows how stage 3 reruns from stage 2, but the automatic retry would
  need the same mapping, and a rerun could no longer resend the approval card for the same package.
- **Save the facts on `hrs_rfp` (chosen).** One new column covers every way into stage 9.

For the repeated answer:

- **Delete proposals when an addendum restarts the run.** This loses earlier answers that a reviewer may have approved.
- **Look up the code first (chosen).** A Draft is replaced with the newer answer. An entry that has left Draft isn't changed.

## Decision

- Stage 8 saves `{files, pages}` to the new memo column `hrs_rfp.hrs_packagefacts`. Stage 9 reads it. If it's empty, the
  stage fails with "No saved package from stage 8. Rerun from stage 8."
- `RFP - Route Gap To SME` looks up `PROP-<key>-<requirement>`. With no entry, it creates one. With a Draft, it replaces the
  question and answer text. With an Approved or Retired entry, it changes nothing. It links the gap to the entry in every case.
- The check that resumes the pipeline now also runs after a failed answer step. The answer is written first, so a failed
  library write no longer strands the run, and a gap whose answer failed to save stays Routed and still holds it.

- `RFP - Hook Pre Tool Use` writes a "Tool call denied" row to `hrs_runlog`, because the Post tool use hook doesn't fire
  for a denied call. The denial still goes through if that write fails.
- `activate-flows.ps1` and `apply-flows.ps1` leave a flow alone when it's unchanged, on and started, and they read the state
  again after a pause. Two flows were seen off a minute after being reported as started. The cause is unconfirmed; a stop
  landing after the start is the leading explanation.

## Consequences

- RFPs last assembled before this change have no saved facts. They need one rerun from stage 8 before a stage 9 rerun works.
- A rerun's `LIBRARY-DRAFT-PRESENT` flag could name proposals from an earlier pass of the same RFP. Run
  `20261101-184710-D-test` scored 0.818 on flag precision for mostly this reason. ADR-0033 removes those flags.
- A failed library write now leaves the flow run reporting success, with the failure visible only in the action history.
