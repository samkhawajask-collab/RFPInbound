# ADR-0029 — Whole-set prompts split only above the pilot's largest call; scale is shown by a 500-requirement run

- **Status:** Accepted
- **Date:** 2026-10-31
- **Deciders:** Solution architecture, with the user's approval in the Slice 7 plan (synthetic run plus code)
- **Related:** [ADR-0015](0015-leaf-steps-are-prompts-called-by-agent-flows.md), [ADR-0018](0018-one-pipeline-flow-chained-through-dataverse.md), [ADR-0019](0019-meaning-based-candidate-recall.md), [ADR-0021](0021-self-consistency-for-judgment-prompts.md), [ADR-0028](0028-cost-and-latency-instrumentation.md), ARCHITECTURE.md §14.1

## Context

ARCHITECTURE.md §14.1 sizes the design for 10× the pilot: 40 RFPs in flight, up to 500 requirements each, and an
answer library of 2,000 entries. Through Slice 5:

- **Stage 5 batches.** Shortlist, Select and Draft ran in batches (`stages.py` notes that one call over about 50
  requirements came back cut short).
- **Whole-set prompts.** Six prompts still sent the whole set in one call: Classify, Identify ×3, Judge
  Relations ×3, Screen Rules, Adjudicate and Draft Clarifications.
- **Library comparisons.** `library.py` re-tokenized every library answer once per requirement.
- **One run at a time.** The pipeline runs one RFP at a time per environment (ADR-0018).

## Decision

**Batch sizes at or above the pilot's largest single call.** The pilot maxima were read from the Slice 5 run
history. RFPs A, B and D therefore still make exactly one call per prompt and behave as before; only a larger
RFP is split.

| Prompt | Pilot maximum | Batch size |
|---|---|---|
| Classify | 90 requirements (B) | 90 |
| Identify ×3 | 119 candidates (B) | 120 |
| Judge Relations ×3 | 92 requirements (B) | 100 |
| Screen Rules | 92 requirements (B) | 100 |
| Adjudicate | 17 hits (B) | 20 |
| Draft Clarifications | 3 items | 5 |

**Mechanics.**
- rfp-tools returns the batches, either from new operations (`StageClassifyBatches`, `StageRelationBatches`) or as
  extra fields on existing ones (`candidateBatches`, `itemBatches`, `batches`).
- The flow loops over them four at a time. It collects each pass's outputs in a run variable and hands the list
  back, and rfp-tools merges it (`batched_prompts` in `tools/flowgen/build.py`).
- The pipeline is at 440 of 500 actions after this ADR and ADR-0026, 0027 and 0028.

**Relations above 100 requirements.**
- Requirements are grouped by category and packed into batches of at most 100. Duplicates and contradictions
  sit mostly within a category.
- Relations *across* batches are not looked for at that size. This is the one place where scale costs recall,
  and it is recorded here rather than hidden.

**Unknown IDs in a relation are dropped with a warning, not a stop.**
- In the first SCALE-500 run, the relations prompt named company-content file names as contradiction members.
- Such a finding can't be written to any requirement row, so `relation_updates` drops it and reports it under
  `warnings`, as the identify step already does.
- Missing or repeated IDs in the outputs that drive rows (classification, selection, adjudication, drafts) still
  stop the stage.

**Library comparisons are cached.**
- `_tokens` and `_vector` are memoized per text.
- 500 requirements against 2,000 approved answers took 16.6 s for shortlist and selection items. It now takes
  1.8 s on a developer machine.
- `tools/tests/test_scale.py` holds the bound at 20 s, under the 120 s a flow's synchronous call waits.

**One RFP at a time stays.** Forty RFPs in flight queue on the pipeline's trigger, with concurrency 1, per
ADR-0018. Throughput is measured below. The path to parallel RFPs is recorded, not built: one pipeline run per
RFP, which needs per-RFP variables, since flow variables are per run.

**Evidence: SCALE-500.**
- `eval/scale/make_scale_rfp.py` builds a 500-requirement Word RFP (499 distinct texts).
- It uses RFP A's document as the template, with RFP A's requirements, RFP C's questionnaire questions, and the
  library's questions and rule triggers phrased as requirements. These are inputs only; no gold data is used.
- `eval/scale/run.ps1` runs it once in dev with the card flows switched off. It closes gaps by script and writes a
  usage report; there is no answer key, so nothing is scored.

## Results

SCALE-500 in dev, 2026-10-31 (`eval/reports/20261101-000716-S-dev-usage.md`).

It took three attempts, and the first two found two defects that only appear at scale. Both are fixed and covered
by `tools/tests/test_scale.py`.

1. **Unknown IDs in a relation.** The relations prompt named company-content file names as contradiction members.
   Gate `Gate_Relations` stopped the run. Fixed: such a finding is now a warning (above).
2. **Duplicate chains.** Batched relations produced chains (A duplicates B, B duplicates C). A requirement whose
   duplicate target was itself a duplicate got no compliance value: 18 of 500, so gate G4 stopped stage 6.
   Fixed:
   - Stage 4 points every duplicate at the root of its chain and drops a link that would close a loop.
   - Stage 6 follows a chain to its root, and judges a requirement with no reachable root on its own.

One clean pass, stages 1-3 from attempt 2 and 4-9 from the rerun after the fix:

| Stage | Machine minutes | Prompt credits |
|---|---|---|
| 1 Normalize | 0.9 | 10.5 |
| 2 Extract (Classify in 6 batches) | 1.0 | 61.5 |
| 3 Reconcile | 0.1 | 0 |
| 4 Analyze set (5 batches × 3 relations passes) | 1.4 | 238.5 |
| 5 Select answers | 1.8 | 694.5 |
| 6 Policy and drafting | 1.7 | 499.5 |
| 7 Gaps and clarifications (25 gaps) | 0.7 | 27.0 |
| 8 Assemble | 0.5 | 153.0 |
| 9 Approval | 0.1 | 0 |
| **Total** | **8.2** | **1,684.5** |

- **Agent-flow actions:** an estimated 786 credits across both passes of stages 4-6 together, about 550 for one
  pass. The total is therefore about 2,250 credits, inside ADR-0028's 2,500-3,000 estimate.
- **Machine time:** 8.2 minutes for 500 requirements, against §14.2's 40-minute target for 60. The parallel batch
  loops keep each stage near its pilot duration.
- **Throughput for 40 RFPs in flight:** they would queue at about 8 minutes of machine time each, about 5.5 hours
  for all 40, plus human waits, which overlap. That is acceptable for the pilot and is the number to revisit
  before production.
- **Run outcome:** 387 flags and 25 gaps were raised. The run reached stage 9 and the approval request (the card
  flow was switched off).

## Consequences

- Pilot behavior is unchanged by construction, and the exit run checks it.
- At 500 requirements, cross-category duplicates and contradictions go unflagged.
- The dev answer library stays at its seeded 212 entries. The 2,000-entry library is shown by the load test, not
  by a run.
