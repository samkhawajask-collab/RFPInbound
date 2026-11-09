# ADR-0003 — Requirement identity and source location model

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0002](0002-document-ingestion-service-per-format.md), [ADR-0010](0010-addendum-reconciliation-as-immutable-revisions.md)

## Context

Requirements need an identity that is stable across pipeline re-runs, addendum revisions and
evaluation scoring, and a location precise enough that a human can find the original text on the
page.

The four formats disagree about identity:

| RFP | Buyer IDs? | Example location |
|---|---|---|
| A | Yes — `CV-001` … `CV-060` in a table column | `Section 4.1, table row CV-001` |
| B | **No IDs anywhere in the PDF** | `Section 3.4, Table 3-1, item a` · `Appendix C, item C-01` · `Appendix D, paragraph D.1` · `… (page 7)` |
| C | Yes — `Ref` and `ID` columns | `Questionnaire tab, row 14, Ref 12` |
| D | Yes — requirement numbers, recovered by OCR | `Section 5.10, requirement THPA-036` · `Addendum No. 1, item B.4` |

RFP B is the hard case: identity must be *assigned*, and it must be assigned the same way every
time or the evaluation harness cannot match extracted requirements to the answer key at all.

Two further complications: one RFP B requirement spans a page break (pages 7-8), and RFP D's IDs
come through OCR and can be misread.

## Options considered

### Option A — Always assign our own IDs

Ignore buyer IDs; number everything in document order.

- **For:** One identity rule for all formats; immune to OCR errors in the buyer's ID.
- **Against:** Breaks the hard rule to keep the buyer's own IDs where they exist. The buyer's
  compliance matrix, the clarification letter and the approval conversation all reference the
  buyer's IDs. A proposal that renumbers the buyer's requirements is harder to evaluate and looks
  careless.

### Option B — Content hash as identity

Identity is a hash of the normalized requirement text.

- **For:** Stable across re-runs without needing document order; duplicates collapse naturally.
- **Against:** An addendum that modifies a requirement changes its hash, so the modified
  requirement looks like a delete plus an add and its history is lost. Exactly the wrong behavior
  for ADR-0010. OCR noise also changes the hash.

### Option C — Buyer ID where it exists, assigned ID in document order otherwise; location derived from the source block *(chosen)*

- **For:** Honors the buyer's numbering; gives RFP B a deterministic, reproducible scheme;
  separates identity (stable) from text (may change by addendum).
- **Against:** Document order for RFP B depends on correct section ordering during normalization —
  if the appendices are read out of order, every ID shifts and recall collapses. Needs an explicit
  ordering rule and a test.

## Decision

**Option C.**

**Identity.**

- Natural key is `(hrs_rfp, hrs_reqid)`.
- Where the buyer prints an ID, that ID is used verbatim.
- Where the buyer prints none, assign `<PREFIX>-NNN` in document order, zero-padded to three
  digits, with the prefix taken from the solicitation (`MCHD` for RFP B).
- Document order is defined as: section order from the normalization pass, then block index within
  the section. The ordering rule is a unit-tested function, not an emergent property.
- IDs are allocated once, at first extraction, and never reallocated. A re-run matches existing
  requirements on `(rfp, location, normalized text)` before allocating anything new.

**Location.**

- Every requirement points at the `hrs_documentblock` it came from (`hrs_sourceblock`).
- `hrs_location` is **derived** from that block, never typed, and follows the format's own
  vocabulary — section and table row for Word, section/table/appendix item plus page for PDF, tab
  and row and Ref for Excel.
- Where a requirement spans more than one block, the location names the first block and the page
  range. This is what makes RFP B's MCHD-012 (pages 7-8) a single requirement with a correct
  location rather than two fragments.
- `hrs_section` is stored separately from `hrs_location` so the compliance matrix can group by
  section.

**OCR-derived IDs.** A buyer ID recovered by OCR below the confidence threshold raises
`OCR-LOW-CONFIDENCE` and must be verified before gate G6. An unverifiable ID falls back to an
assigned ID and the discrepancy is recorded on the requirement.

## Consequences

- RFP B's extraction recall depends on the section-ordering function, so that function gets a
  dedicated test in slice 3 before any extraction tuning.
- The evaluation harness needs two matching rules: by `req_id` for A, C and D; by `location` plus a
  text-similarity threshold for B. Reported separately so an ID-offset bug is not read as a recall
  failure.
- Identity survives addendum modification: THPA-036 keeps its ID while its text changes, and the
  pre-change text is preserved in `hrs_originaltext`.
- Duplicate requirements keep their own IDs and point at the first member through
  `hrs_duplicateof` — they are cross-referenced, never merged, because the buyer still expects an
  answer at both numbers.
