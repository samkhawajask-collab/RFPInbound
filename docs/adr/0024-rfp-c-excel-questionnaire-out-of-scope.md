# ADR-0024 — RFP C (Excel questionnaire) is out of scope

- **Status:** Accepted
- **Date:** 2026-10-31
- **Deciders:** The user (product owner), in the Slice 3 session
- **Related:** [ADR-0002](0002-document-ingestion-service-per-format.md), [ADR-0009](0009-evaluation-harness-outside-the-platform.md), ARCHITECTURE.md §6.2 and §16

## Context

The phased plan (ARCHITECTURE.md §16) built one format per slice: RFP A (Word), RFP B (native PDF),
RFP C (Excel questionnaire), then RFP D (scanned PDF plus addendum). Slices 1–3 are done. RFP A and RFP B
meet the 2-of-3 rule in dev (PR #1, PR #2). Slice 4 would add RFP C:
- an Open XML questionnaire path;
- the buyer's own 250-word formula;
- Yes / No / Partial / N/A response codes;
- evidence references;
- a return inside the buyer's unchanged workbook.

## Decision

**RFP C and Slice 4 are removed from scope**, at the user's direction, to reduce scope. The next slice is
Slice 5, RFP D (scanned PDF), followed by Slice 6 (addendum reconciliation) and Slice 7 (hardening).

## Consequences

- Excel questionnaires aren't a supported input. `NormalizeDocument` already rejects files that aren't
  .docx or .pdf. The rejection message, which still says Excel arrives in Slice 4, gets updated when that
  code is next touched.
- The design text for Excel stays in ARCHITECTURE.md, marked out of scope, so it can be picked up again
  without rework. This covers §6.2's Excel row, the RFP C word-counting rule, the RFP C cost estimate,
  assumption A5 and question Q8.
- These are left as they are, because removing them is a schema or contract change with no benefit:
  - the `Xlsx` option in `hrs_format`;
  - the `C` thresholds in `eval/thresholds.json`;
  - RFP C's rows in the synthetic dataset.
- No agent, prompt or flow reads RFP C.
- Slice 7's exit criterion becomes "RFPs A, B and D pass in one run".
- The evaluation harness still accepts `-Rfp C`, but nothing will run it.
