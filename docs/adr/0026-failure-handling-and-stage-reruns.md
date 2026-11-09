# ADR-0026 — A failed stage undoes what it created and retries once; reruns use the same rule

- **Status:** Accepted
- **Date:** 2026-10-31
- **Deciders:** Solution architecture, with the user's approval in the Slice 7 plan
- **Related:** [ADR-0010](0010-addendum-reconciliation-as-immutable-revisions.md), [ADR-0018](0018-one-pipeline-flow-chained-through-dataverse.md), [ADR-0020](0020-scoring-runs-complete-human-waits-by-script.md), [ADR-0023](0023-document-intelligence-from-rfp-tools-with-managed-identity.md), ARCHITECTURE.md §14.4

## Context

ARCHITECTURE.md §14.4 promised backoff on Document Intelligence throttling, one retry of a failed stage, and
stages that are idempotent on the natural key. Through Slice 5 none of that was built:

- `pdf_layout.py` made one call with no backoff, and rfp-tools answered 500 for any downstream failure.
- One catch-all `On_failure` scope marked the RFP Failed with a run-log row that named no stage, and left the
  failing stage's own row at Started.
- Stages **create** rows; they don't upsert them (ADR-0010 amendment 1 already recorded this). A stage that
  failed halfway, run again, would duplicate requirements, flags, responses or gaps.
- `rewind-run.ps1` hard-coded what each of stages 4-9 wrote and could not rerun stages 1-3.

The pipeline is one flow at 337 of the platform's 500 actions per workflow
([limits](https://learn.microsoft.com/en-us/power-automate/limits-and-config), checked 2026-10-31), so a handler
per stage was too expensive.

## Options considered

1. **Upsert on the natural key in every stage.** The right end state, but every `write_rows` becomes a list,
   match and branch, roughly doubling the write actions and the flow-action credits. Rejected for now.
2. **Clear each stage's own outputs at its start, from a per-stage table.** Needs the table in three places
   (flow, rewind script, Orchestrator tool) and still runs list actions on every fresh run.
3. **Undo by creation time.** Every row a stage creates for an RFP is created after the stage starts. Removing
   the RFP's rows created since a point in time therefore undoes exactly the stages that started after it.
   One rule, no per-stage table, and nothing runs on a run that doesn't fail. **Chosen.**

## Decision

**Undo rule.** For one RFP, delete the rows created since time *t*, dependents first: approvals,
clarifications, gaps, responses, flags, requirements, document blocks (through document → revision → RFP), and
Draft library proposals from its gaps (`PROP-<key>-*`). `created_since_tables` in `tools/flowgen/build.py` and
the table in `scripts/rewind-run.ps1` list the same tables in the same order.

**Updates are rewritten, not undone.** Compliance values and drafts are overwritten by stages 6 and later.
Stage 4 now starts by clearing the relation columns (duplicate lookup, ambiguous, out of scope, contradiction
group) on the rows that carry one. That replaces stage 3's partial clear and closes the open item that
`hrs_duplicateof` survived an addendum reprocess.

**Failure handler (one scope after stage 9).**
- It finds the failing stage from its own run-log row (status Started, this flow run's correlation ID).
- It undoes rows created since that row's start, marks the row Failed with the reason, and retries once:
  `hrs_stage = N-1`, status Not started, then Running. The trigger runs one pipeline at a time, so the retry
  starts when this run ends.
- If the stage's previous attempt already failed with "retrying once", it marks the RFP Failed instead.
- Gate failures never reach it: a gate marks the RFP Failed and terminates the run, and gates never auto-waive.
- A failure before any stage started (reading the source document, company content) marks the RFP Failed
  with a run-log row that says so.

**Reruns.** `rewind-run.ps1 -FromStage N` (and the Orchestrator's Rerun Stage tool, ADR-0030) applies the undo
rule with *t* = the start of stage N's latest pass. N may now be 1-9. A rerun from stage 3 runs from stage 2,
and any rerun from stage 2 or earlier resets `hrs_currentrevision` to 0: an addendum's text and status changes
apply to rows stage 2 creates, so stage 3 must apply every addendum again. Run-log rows are kept as the audit
trail; the eval harness scores the passes since a run's start (ADR-0028).

**Transient errors.**
- `pdf_layout._request` retries 408, 429 and 5xx responses and network errors 3 times, with exponential backoff
  (2, 4, 8 s) or the `Retry-After` value. It then raises `TransientError`, and so does a Layout analysis that
  doesn't finish in time.
- rfp-tools answers **503** for `TransientError` (with `Retry-After: 10`) and keeps 400 for bad input.
- A Layout analysis the service rejects is a 400: retrying won't help.
- Every rfp-tools connector action gets an explicit exponential retry policy (3 retries, 10 s). Prompt actions
  already had one (3 retries, 20 s).
- A prompt whose output can't be parsed fails the consuming action. The failure handler catches that like any
  other stage failure.

**Failure drill hook.** App setting `RFPTOOLS_FAULTS="<operation>:<n>"` makes rfp-tools fail the first *n* calls
of one operation on an instance with a 503. It is unset except during a drill (docs/RUNBOOK.md).

## Consequences

- A stage that fails halfway leaves no duplicates, and a retry needs no manual cleanup. The handler adds 35
  actions to the pipeline (372 of 500 after this change).
- The undo rule assumes nothing but the pipeline and its own card flows creates these rows for an RFP while a
  stage runs. That holds: the gap flow creates library proposals (covered by the rule), and the OCR and approval
  flows only update rows.
- Deleting gaps cancels nothing in Teams. A gap card whose row was undone fails when answered. Withdrawing such
  cards is ADR-0027's job (Q9).
- Upserts and selective reprocessing stay deferred (ADR-0010 amendment 1).
- **Verified 2026-10-31:** the Dataverse connector's update clears a lookup when passed null
  (`hrs_duplicateof@odata.bind: null`). The first rerun of RFP A's stage 4 cleared all 5 related rows,
  duplicate links included.
