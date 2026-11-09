# ADR-0022 — Prose requirement extraction: code segments, a prompt judges, code assigns

- **Status:** Accepted
- **Date:** 2026-10-30
- **Deciders:** Solution architecture, with the user's approval in the Slice 3 plan
- **Related:** [ADR-0002](0002-document-ingestion-service-per-format.md), [ADR-0003](0003-requirement-identity-and-source-location-model.md), [ADR-0015](0015-leaf-steps-are-prompts-called-by-agent-flows.md), [ADR-0021](0021-self-consistency-for-judgment-prompts.md)

## Context

RFP A's requirements sit in tables that carry the buyer's IDs, so stage 2 extraction is code. RFP B (a
native PDF) has no buyer IDs, and its requirements are spread across:
- numbered prose paragraphs, where one paragraph can hold several requirement sentences next to
  background sentences and general statements of preference;
- a lettered table (Table 3-1, items a–g);
- appendix items in three numbering styles (`C-01` rows, `D.1` paragraphs, `E-1` rows), next to
  appendices that are forms or data elements.

Hard rule 6 makes requirement ID assignment code. The thresholds are tight (extraction recall and
precision 0.92), and the harness matches B's requirements on location plus text, so the text must be the
buyer's own words.

## Options considered

- **A — Rules only:** a modal verb ("shall", "must", "requires") marks a requirement. It's
  deterministic, but it accepts background sentences that use "will", misses contract terms written
  passively, and can't tell a specific preferred capability from a general aspiration.
- **B — Free-text LLM extraction:** a prompt lists the requirements. It can paraphrase, merge or reorder
  them, so the text and document order are no longer guaranteed.
- **C — Code segments, a prompt judges, code assigns** *(chosen)*.

## Decision

**Option C.**

- **Candidates (code, `sentence_candidates`):**
  - Body paragraphs are split into sentences, with the numbered paragraph's own label removed. The
    splitter respects abbreviations such as "e.g." and numerals such as "(24)".
  - A table row with an item label is one candidate: its requirement column, or every non-label cell.
  - Headings, header rows and unlabeled table rows aren't candidates.
  - **A numbered document's unnumbered paragraphs aren't candidates either.** When a document numbers
    at least 10 of its body paragraphs (`3.6.2`, `D.1`), its requirements come from those paragraphs and
    from labeled table items. Its unnumbered paragraphs are introductions, background and submission
    instructions. RFP B says this itself (§3.1: requirements appear "in a numbered paragraph, a table or
    an appendix"). Added after the first scored runs: 9 of the 12 extra requirements came from
    unnumbered paragraphs, even though the prompt named those categories, and none of the matched ones
    did. A document that doesn't number its paragraphs keeps them all as candidates.
  - Each candidate carries its block, section and location.
- **Judgment (prompt, RFP Identify Requirements):** it returns the IDs of the candidates that are
  requirements, under general rules with examples that aren't from any pilot RFP. It runs three times
  in parallel, and a candidate is kept when at least two runs name it (the ADR-0021 pattern). An ID a
  run invents is reported, never used.
- **Assignment (code):**
  - `assign_requirement_ids` gives `<PREFIX>-NNN` in document order. The prefix is the solicitation's
    letters, taken from the opportunity's RFP number (`id_prefix`: "MCHD RFP 26-044" gives `MCHD`).
  - Text stays verbatim, and the location comes from the block (ARCHITECTURE.md §6.2).
  - Mandatory comes from the buyer's priority column, or else from the sentence's modal words, or stays
    empty.
- **Flow:** stage 2 branches on `hrs_format`. Word keeps `ExtractRequirements`. Native PDF runs
  `RequirementCandidates`, the three Identify runs, then `ResolveRequirements`. Both branches feed the
  same classify-and-write path through the `Requirements` variable.
- Stage 2 re-normalizes the source file, as the Word path re-reads the .docx, because Dataverse block
  rows don't keep labels or cells. For a PDF that's one more Layout call per run (about 16 pages for RFP
  B).

## Consequences

- Text and order are exact by construction. Only the yes/no judgment per sentence is a model decision,
  and the majority rule damps run-to-run variance in it.
- A requirement that spans two sentences comes out as two candidates. Whether both are requirements is
  the prompt's call. The answer key's granularity shows up in the extraction metrics.
- The page-break merge happens in normalization (ADR-0023), so a sentence cut by a page break is one
  candidate.
- Cost: three prompt calls per prose RFP at stage 2, each over the full candidate list (217 sentences
  for RFP B).

## Amendment 1 — 2026-10-31: requirements led by a buyer ID are taken by code

**Context.** RFP D is scanned prose, but every requirement starts with the buyer's own ID
(`THPA-005 The system must … (M)`). ADR-0003 keeps buyer IDs wherever they exist, and running the
identify prompt over prose that already labels its requirements would only add variance.

**Change.** `requirements.labeled_requirements(blocks)`:
- A block led by a buyer ID (`[A-Z]{2,6}-NNN`, tolerant of OCR spacing such as `THPA -007` or
  `. THPA - 045`) is one requirement with that ID. Two IDs that Layout merged into one paragraph are split.
- The (M)/(D) marker gives Mandatory or Desirable, including a marker OCR left on a line of its own.
- Location: `Section 5.9, requirement THPA-036 (page 5)`.
- When a document has at least 10 such blocks, stage 2 takes these and skips the identify prompt
  (`RequirementCandidates` returns `mode: labeled`). Otherwise the prose path above applies unchanged.

**Consequences.** RFP D's 50 base requirements come out in document order with their own IDs and no
prompt call. An ID OCR misreads beyond the tolerance stays misread until a person corrects it (ADR-0025),
and the harness counts it as a miss plus a spurious requirement.
