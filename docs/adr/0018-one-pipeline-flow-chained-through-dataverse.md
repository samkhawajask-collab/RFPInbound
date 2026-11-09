# ADR-0018 — One pipeline flow with stage scopes, chained to the human-loop flows through Dataverse

- **Status:** Accepted
- **Date:** 2026-10-29
- **Deciders:** Solution architecture
- **Related:** [ADR-0001](0001-deterministic-workflow-spine-agents-at-the-leaves.md), [ADR-0011](0011-solution-source-layout-and-regeneration.md), [ADR-0015](0015-leaf-steps-are-prompts-called-by-agent-flows.md)

## Context

§3.4 designed one flow per stage (`RFP - Normalize Document`, `RFP - Extract Requirements`, and so on),
called in order by `RFP - Run Pipeline`. While building Slice 1, two facts changed the trade-off:

- **Calling one flow from another** needs a child-flow action with a manual or Skills trigger and
  typed inputs and outputs. Every stage then needs its own input and output contract, and a failure
  in a child surfaces in the parent only as a generic action failure.
- **The pipeline has to stop for days** at stage 7 (SME gaps) and stage 9 (approval). A flow can't
  wait across those pauses without holding a run open against capacity. §3.4 already kept the waits
  in separate cloud flows. The open question was how those flows hand control back to the pipeline.

## Options considered

- **A — One flow per stage, called as child flows** (the original §3.4). Small flows, but nine
  contracts to keep in step, and the parent still has to be re-entered after each human wait.
- **B — One pipeline flow, with each stage in its own scope, re-entered through a Dataverse trigger**
  *(chosen)*.
- **C — Each stage flow triggers the next through a Dataverse row change.** No central place to see
  or restart the run, and stage order lives in nine separate triggers.

## Decision

**Option B.**

- **The pipeline is `RFP - Run Pipeline`, one agent flow.**
  - It triggers when `hrs_rfp.hrs_status` changes to Running.
  - Each stage is a scope guarded by `hrs_stage < N`, so a re-entered run skips completed stages.
  - Each stage writes its own `hrs_runlog` row and checks its gate.
  - One `On_failure` scope marks the RFP Failed with the failing stage.
  - Trigger concurrency is 1: one pipeline run at a time in the environment.
- **Human waits hand back through Dataverse, not through flow calls.**
  - Stage 7 creates the `hrs_gap` rows, sets the RFP to *Awaiting human* and ends.
  - `RFP - Route Gap To SME` triggers on each new gap row and runs in parallel, one run per gap.
  - When no gap is still Routed, it sets the RFP back to Running, and the pipeline resumes at stage 8.
  - Stage 9 works the same way. It creates the `hrs_approval` row, `RFP - Request Approval` triggers
    on it, and reject sets the RFP back to Running at stage 8.
- **Restart is a data change.** Setting `hrs_stage` to N−1 and the status to Running reruns from stage N.
  `scripts/reset-run.ps1` clears a run completely in dev.
- **Generated, not hand-built.**
  - `tools/flowgen/build.py` generates the four flows into `solution/spec/flows/`.
  - `scripts/apply-flows.ps1` applies them to dev.
  - The flows are then unpacked into `solution/src` (ADR-0011).

## Consequences

- **Four flows, not thirteen:** Receive, Pipeline, Route Gap and Request Approval. §3.4 is updated.
  `RFP - Draft Clarification Letter`, `RFP - Assemble Proposal` and `RFP - File Output` are scopes or
  branches inside the pipeline and approval flows.
- **The run history of one flow shows the whole run.** Per-stage detail is in `hrs_runlog`.
- **A rerun of stage 7 leaves the earlier cards live in Teams.** In Slice 1, an answer to a
  superseded card went to a cancelled run and was lost. **Open:** the gap flow should withdraw or
  update superseded cards (Slice 7 hardening).
- **Concurrency 1 on the pipeline** serializes RFPs in one environment. That is acceptable for the
  pilot. It must be revisited if several RFPs have to be processed at once.
- **Only the pipeline may be single-run.** Setting it on the gap flow serialized the cards: in
  Slice 1, 19 cards queued behind each other.
