# ADR-0016 — Templates are filled in place by rfp-tools; Word Online only converts to PDF

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Amends:** [ADR-0008](0008-proposal-assembly-word-template-content-controls.md) (the population mechanism only; the multi-file package and limit enforcement stand)
- **Related:** [ADR-0013](0013-rfp-tools-in-python.md)

## Context

ADR-0008 chose Word Online (Business) `Populate a Microsoft Word template`. That needs a one-off step
to convert the supplied `{{Token}}` placeholders into named content controls, including
repeating-section controls for the requirements matrix and the exceptions table. It also has to work
within the connector's limits: a premium connector, 100 calls per 60 seconds, no nested repeating
sections, and no rich text.

Inspecting the supplied templates in Slice 1 (2026-10-28) showed:

- every placeholder sits **whole inside a single run**: no token is split across runs;
- the repeating parts are **ordinary table rows** whose cells hold tokens (`{{ReqID}}` …,
  `{{n}}` …);
- three placeholders (`{{ScheduleTable}}`, `{{TeamTable}}`, `{{ExceptionsTable}}`) stand alone in a
  paragraph where a table belongs;
- two placeholders carry trailing guidance in parentheses that must not reach the buyer.

## Options considered

### Option A — Convert to content controls and populate through Word Online (ADR-0008)
- **Against:** the template conversion is fiddly hand-built XML (`w15:repeatingSection`); it adds a
  premium connector call per document and the throttling limits; and nothing about it can be unit
  tested outside the platform.

### Option B — Fill the buyer's `.docx` in place in rfp-tools *(chosen)*
- **For:**
  - **Template fidelity by construction:** text is replaced inside the existing runs, so every
    style, header, footer and section property is the template's own.
  - **No template conversion:** the supplied files are used as they are.
  - **No connector limits on population.**
  - **Unit-tested locally** against the real templates.
  - **Placeholders can't ship:** an unfilled token raises an error.
- **Against:** rich content (tables) is built with python-docx in a basic table style, not authored
  by a designer. That's acceptable for the schedule, team and exceptions tables.

## Decision

**Option B.** `rfp_tools.assembly`:

- `fill_template(template, scalars, tables, repeating_rows)` fills scalar tokens (body, headers and
  footers). It clones repeating rows once per item, turns `{{XTable}}` paragraphs into tables, splits
  blank-line values into same-style paragraphs, strips template guidance, and **raises on any
  unfilled token**.
- `split_sections(filled, excluded_headings)` produces the **governed narrative** and the **excluded
  components** as separate files, split at Heading 1 boundaries (ADR-0008's package structure). The
  title page stays with the governed narrative unless the RFP excludes it.
- **Word Online (Business) is used only for `Convert Word Document to PDF`,** so `count_pages` can
  measure the governed narrative.
- The Excel compliance matrix is filled cell by cell (`fill_compliance_matrix`), which preserves the
  validation lists and Summary formulas, as ADR-0008 required.

## Consequences

- ADR-0008's "one-off template preparation" task is removed. The originals in `06_Templates` are
  used unmodified.
- The Word Online connection reference stays, for PDF conversion only. Its 10 MB input limit still
  applies to the governed narrative, which is far smaller.
- The narrative sections (executive summary, solution, implementation, training, support, security)
  need text. A prompt, `RFP Draft Section`, writes each one from the drafted responses and the
  company content, within a per-section word budget, alongside the per-requirement prompts in
  ADR-0015. If the page count goes over the limit, the shortening loop in ADR-0008 lowers the budget
  and redrafts.
