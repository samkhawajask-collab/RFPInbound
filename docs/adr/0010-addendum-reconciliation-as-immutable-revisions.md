# ADR-0010 — Addendum reconciliation as immutable revisions

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0003](0003-requirement-identity-and-source-location-model.md)

## Context

An addendum can change almost anything. RFP D's Addendum No. 1 does five different kinds of damage
at once:

| Item | Requirement | Change |
|---|---|---|
| 1 | THPA-036 | **Modified** — availability 99.5% → 99.95%, which newly breaches policy rule R-02 and flips compliance from Full to No |
| 2 | THPA-010 | **Deleted** — must not be answered |
| 3 | THPA-020 | **Promoted** — Desirable → Mandatory, text unchanged |
| 4 | THPA-051 | **Added** — and it is a gap, with no approved library answer |
| — | RFP level | Due date 2026-11-24 → **2026-12-08**; technical proposal page limit 25 → **20** |

Plus a procedural obligation: the addendum must be acknowledged on the signature page, and failure
to acknowledge may result in rejection.

So reconciliation has to handle four requirement-level change kinds and two RFP-level changes, and
it has to do so without destroying the history of what the requirement said before. A reviewer and
an auditor both need to see that THPA-036 *used to* say 99.5%.

Addenda also arrive after work has started. By the time Addendum No. 1 lands, requirements may
already be drafted, flagged and routed to SMEs.

## Options considered

### Option A — Re-run the pipeline from scratch on the combined document set

Treat base plus addendum as one new input.

- **For:** Simplest to reason about; one code path; no diff logic.
- **Against:** Throws away every draft, flag, gap routing and SME answer already produced, and costs
  a full run's credits. It also loses the *fact* of the change: there would be no record that the
  availability requirement moved, which is exactly the thing a reviewer most needs to see. And it
  cannot express "deleted" — a withdrawn requirement would simply vanish.

### Option B — Mutate the requirement rows in place

Update text, mandatory flag, status.

- **For:** Cheap; keeps IDs stable.
- **Against:** No history. The pre-change text is gone, so "what did it say before" is unanswerable,
  and a re-run cannot tell a genuine change from its own earlier write. Re-running the addendum
  stage twice would not be idempotent.

### Option C — Immutable revisions plus a deterministic set diff, applied as typed changes *(chosen)*

Each document set is a revision; reconciliation diffs revision N against N−1 and applies typed
changes to the live requirement rows, preserving the previous text.

- **For:** History is preserved, the change kinds are explicit and flaggable, only affected
  requirements are reprocessed, and the stage is idempotent because the diff is a function of two
  immutable inputs.
- **Against:** More schema (`hrs_rfprevision`) and a reword-matching problem: an addendum that
  reworded a requirement rather than restating it verbatim would otherwise look like a delete plus an
  add.

## Decision

**Option C.**

**Revision model.** `hrs_rfprevision` is immutable. Revision 0 is the base solicitation; revision 1
is Addendum No. 1; further addenda increment. Each revision owns its `hrs_rfpdocument` rows and
therefore its own `hrs_documentblock` rows. Nothing in a prior revision is edited.

**Diff.** `diff_requirement_sets(revN-1, revN)` is deterministic:

1. Match on requirement ID where the addendum cites one — which is the normal case, because an
   addendum references the requirement it changes.
2. Unmatched pairs within the same section are shortlisted and passed to the **RFP Equivalence**
   agent, which judges whether an unmatched pair is the same requirement reworded. This is the only
   model involvement in the stage.
3. Classify each item as **Modified**, **Deleted**, **Added** or **Promoted** (an attribute-only
   change, such as Desirable → Mandatory, with the text unchanged).

**Application.**

| Change | Effect on `hrs_requirement` | Flag |
|---|---|---|
| Modified | `hrs_requirementtext` updated; prior text preserved in `hrs_originaltext`; `hrs_introducedinrevision` unchanged; downstream state for that requirement invalidated | `ADDENDUM-CHANGE` (Info) |
| Deleted | `hrs_status = Withdrawn`; compliance becomes `Withdrawn`; no response is drafted or kept | `ADDENDUM-WITHDRAWN` (Info) |
| Added | New requirement row, `hrs_introducedinrevision = N`, location points at the addendum (`Addendum No. 1, item B.4`) | `ADDENDUM-CHANGE` (Info) |
| Promoted | `hrs_mandatory` updated; text untouched | `ADDENDUM-CHANGE` (Info) |

**RFP-level changes** are applied to `hrs_rfp`: `hrs_duedate`, `hrs_qadeadline`, `hrs_pagelimit`,
`hrs_wordlimitperanswer`, and the required-forms list — which gains the addendum acknowledgement.
A page-limit reduction re-runs the limit check at gate G6 even if assembly had already passed.

**Selective reprocessing.** Only requirements the diff touched are reprocessed, and only through the
stages that depend on what changed:

- text changed → re-select answer, re-draft, re-evaluate policy rules, re-derive compliance;
- mandatory flag changed → re-derive compliance only (and re-check the Summary sheet's "mandatory not
  Full" count);
- added → full processing from stage 5;
- deleted → drop downstream state.

Because every stage is idempotent on `(hrs_rfp, hrs_reqid)`, selective reprocessing is a re-run of
those stages for a filtered set, not a special code path.

**Acknowledgement.** The addendum acknowledgement is added to the required-forms checklist, and gate
G6 fails if a required form is unaccounted for.

## Consequences

- The 6 addendum assertions are all-or-nothing in the evaluation: four requirement changes plus the
  due date and the page limit. `ADDENDUM-CHANGE` and `ADDENDUM-WITHDRAWN` carry 1.00 recall
  thresholds because the diff is deterministic.
- THPA-036 demonstrates the cascade the design has to get right: a modified text crosses a numeric
  threshold, fires a Block rule, changes compliance from Full to No, and raises the approval level by
  the risk route. Any link broken makes the proposal wrong in a way the approver would not see.
- THPA-036 is also the OCR risk case — the base value arrives through OCR of a scanned page. The
  low-confidence queue and this ADR together are what stop a misread digit from silently deciding
  compliance.
- `hrs_documentblock` grows per revision rather than being replaced, which is intended: the base
  scan and the addendum are separately citable.
- A withdrawn requirement stays visible in the compliance matrix with compliance `Withdrawn`, so a
  reviewer can see it was considered and removed rather than missed.

## Amendment 1 — 2026-10-31: the addendum is read as a change notice; reprocessing is full

**Context.** RFP D's Addendum No. 1 is a native PDF change notice, not a restated requirement set. It
cites each requirement it changes by ID (items B.1–B.4), quotes revised text, and states the RFP-level
changes in prose (items A.1, A.2). There is no second requirement set to diff, so `diff_requirement_sets`
and the RFP Equivalence judge have nothing to work on.

**Change.**
- **Interpretation (prompt, RFP Interpret Addendum).** Given the addendum text, one line per block with
  its item label (`[B.4] …`), and the RFP's requirement IDs, it returns typed changes (`modify`,
  `withdraw`, `promote`/`demote`, `add`, each with item, ID, quoted new text, mandatory and section) and
  the RFP-level changes (due date, page limit, acknowledgement required). It runs three times in
  parallel, and a change is kept when at least two runs agree (`addendum.agree`, the ADR-0021 pattern).
- **Validation (code, `addendum.validate`).** A change is applied only when:
  - its item label exists in the addendum;
  - its ID exists in the RFP (or, for `add`, does not);
  - its quoted text appears verbatim in the addendum;
  - a promote or demote names Mandatory or Desirable;
  - a due date or page limit appears in the addendum text.

  Anything else becomes an `ADDENDUM-CHANGE` flag marked "not applied, needs review". The Equivalence
  judge stays deferred until an addendum restates requirements without citing them.
- **Application (code, `addendum.apply`).** The effects table above, unchanged. An added requirement is
  classified and written like stage 2's, with `hrs_introducedinrevision = N` and a location in the
  addendum (`Addendum No. 1, item B.4 (page 1)`); its `ADDENDUM-CHANGE` flag is raised once the row
  exists (`StageAddedFlags`). The RFP gets `hrs_currentrevision`, the new due date and page limit, and
  "Acknowledgement of Addendum No. N" in `hrs_requiredforms`.
- **Intake.** The receive flow records a second file for an existing RFP (matched by RFP number, or by
  buyer name when the file name has no number) as revision N with its own document row. If the RFP is
  past stage 2 and not Running, it sets stage 2 and Running, so the pipeline continues at stage 3.
- **Reprocessing is full, not selective.** After stage 3 applies a revision, it deletes the RFP's
  responses, gaps, clarifications, approvals and flags (keeping OCR and addendum flags), clears the
  relation columns, and stages 4–9 rebuild everything for the revised set. The selective rules above are
  deferred: stages create rows rather than upsert them, so selective reprocessing needs idempotent
  writes first (Slice 7).

**Consequences.**
- Revised text and dates can't be invented: every value applied is found in the addendum by code.
- A full rerun costs one more pass of stages 4–9 per addendum (about the cost of a base run from stage 4).
- **Open:** an addendum that arrives while the run is Running is recorded but not applied until the RFP is
  restarted. `hrs_duplicateof` is not cleared by the reprocess (the rewind script clears it). G6's
  required-forms check stays inert: the acknowledgement is recorded, but nothing yet records which forms
  were provided.

## Amendment 2 — Slice 7 closes the three open items (2026-10-31)

- **Mid-run addendum.** The receive flow still records an addendum that arrives during a run without restarting it.
  The pipeline now checks for a revision newer than `hrs_currentrevision` at two points: when stage 7 starts (before
  any gap card goes out) and when stage 9 ends (after the approval request). If it finds one, it logs "An addendum
  arrived during the run; restarting from stage 3", sets `hrs_stage = 2` and restarts. Stage 3 applies the revision
  and rebuilds stages 4-9, as in amendment 1. An approval card already posted is answered with the withdrawn notice
  (ADR-0027). Exercised by failure drill g (docs/RUNBOOK.md): upload at stage 3, restart logged, revision 1 applied,
  scored PASS (`eval/reports/20261101-031647-D-dev.md`). The drill reached the stage 7 check; the stage 9 check was
  not exercised.
- **`hrs_duplicateof`.** Stage 4 now clears all relation columns, the duplicate lookup included, on the rows that
  carry one before it judges relations (ADR-0026). Stage 3's partial clear was removed.
- **G6 required forms.** The package carries a Required Forms checklist, and G6 checks it against the current
  `hrs_requiredforms`, which includes the addendum acknowledgement (ARCHITECTURE.md §4.2).
