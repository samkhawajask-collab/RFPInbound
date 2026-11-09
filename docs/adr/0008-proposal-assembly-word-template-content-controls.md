# ADR-0008 — Proposal assembly via Word template content controls

- **Status:** Accepted; population mechanism amended by [ADR-0016](0016-templates-filled-in-place-by-rfp-tools.md) (2026-10-28): templates are filled in place by rfp-tools, and Word Online only converts to PDF
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0012](0012-harness-selection-github-copilot-harness.md)

## Context

Stage 8 must produce a Word proposal and a compliance matrix from the supplied templates, within
page and word limits.

The templates are specific. `Proposal_Template.docx` has a cover page and eleven sections — Cover
Letter, Executive Summary, Company Qualifications and Experience, Proposed Solution, Implementation
Approach and Schedule, Training and Change Management, Support and Service Levels, Security and
Hosting, Proposed Team, Exceptions to Terms and Conditions, Requirements Response Matrix — with
placeholder tokens throughout and a five-column matrix table (Req ID, Requirement, Compliance,
Response, Library Answer ID). Section 9 carries the instruction that **every Block or Warn policy
flag must appear there**. `Compliance_Matrix_Template.xlsx` has a twelve-column sheet with data
validation on Compliance, Status and Mandatory, plus a Summary sheet whose COUNTIF formulas must
keep working.

Page limits govern only part of the package, and each RFP excludes different parts: RFP A excludes
the cover letter, forms, résumés and the Requirements Response Matrix from its 30 pages; RFP B
excludes the cover letter, two appendices, résumés and insurance certificates from its 50; RFP D
excludes the signature page, price form and résumés from its 20 (reduced from 25 by the addendum).
RFP C has no page limit at all — it has a 250-word limit per explanation.

## Options considered

### Option A — Let the agent create the document natively

The GitHub Copilot harness "natively creates and edits Word, Excel, PowerPoint, and PDF files".

- **For:** One step, no connector, no template plumbing. Attractive for the narrative sections.
- **Against:** The buyer's template is not a suggestion — section order, headings, the matrix
  columns and the Excel data validation are part of the response contract. A model generating a
  document from scratch will drift from it. For RFP C the workbook must be returned with rows and
  columns unchanged, which rules out regeneration entirely. Page-count control is also lost.

### Option B — Generate HTML, convert to .docx

The pattern used by the gallery's RFP Response Manager sample, which produces a 600-900 word HTML
proposal and converts it.

- **For:** Simple; proven in a shipped sample.
- **Against:** That sample produces a short narrative with no requirements matrix, no compliance
  matrix and no template fidelity. Converting HTML discards the template's styles, content controls
  and the Excel formulas.

### Option C — Populate the supplied templates through Word Online (Business) content controls, assemble the governed narrative as its own file, count pages from the converted PDF *(chosen)*

- **For:** Template fidelity is preserved by construction. Repeating section content controls
  populate the matrix rows. The Excel compliance matrix is written cell-wise so the data validation
  and Summary formulas survive. Page counting becomes unambiguous.
- **Against:** Connector limits must be respected, and the content controls must exist in the
  templates — the supplied files use `{{Token}}` placeholders, which have to be converted to content
  controls once, as a one-off template preparation step.

## Decision

**Option C.**

**Document structure.** The package is assembled as separate files, not one:

1. **Governed narrative** — only the sections the page limit counts. Built from the proposal
   template with the excluded sections omitted.
2. **Excluded components** — cover letter, forms, résumés, references, insurance certificates, and
   the Requirements Response Matrix where the buyer excludes it.
3. **Compliance matrix workbook** — from the Excel template.

This is what makes page counting deterministic: the governed narrative is converted to PDF and its
**whole-file page count** is the governed number. No attempt is made to attribute pages to sections
inside a single large document, which is where page-limit logic usually goes wrong.

**Population.** Word Online (Business) `Populate a Microsoft Word template`, with repeating section
content controls for the requirements matrix and the exceptions table.

**Connector limits, respected explicitly:**

| Limit | Consequence for the design |
|---|---|
| 10 MB maximum input file | Templates stay small; images are not embedded in repeating sections |
| 100 API calls per 60 seconds | The matrix is populated as **one** repeating-section array, not one call per requirement. With 110 requirements, per-row calls would breach the limit. |
| No nested repeating sections | The compliance matrix stays in Excel rather than becoming a nested table in Word |
| No images inside repeating sections | Page-image crops for OCR verification live in the verification queue, not in the proposal |

**Exceptions section.** Gate G5 requires every Block and Warn flag to have a row in section 9 before
assembly completes, matching the template's own instruction. The row carries the rule ID, severity,
the affected requirement, the buyer's requirement as written and the approved position.

**RFP C is different.** There is no proposal document. The buyer's own workbook is completed in
place: column G from the fixed list, column H with the explanation, column J with the evidence
reference. Column I is the buyer's formula and is left alone — our `count_words` reproduces that
formula so we can predict what it will show. Rows, columns, order and names are not touched. (Open
question Q8 confirms this reading.)

**Limits enforcement.** `count_pages` compares the governed narrative's page count against
`hrs_rfp.hrs_pagelimit`; `count_words` checks every per-answer explanation against
`hrs_rfp.hrs_wordlimitperanswer` using the buyer's counting rule. A breach raises `LIMIT-BREACH` and
stops the run at gate G6 — the approval card is never shown for a non-responsive package.

## Consequences

- A one-off template preparation task: convert the supplied `{{Token}}` placeholders into named
  content controls, including repeating sections for the matrix and the exceptions table. This is
  slice 1 work and it changes the template files, so the originals are preserved unmodified.
- Over-length narratives need a shortening loop: when the page count exceeds the limit, the run
  returns to drafting with a reduced word budget for the longest sections rather than truncating.
- The compliance matrix workbook is written cell-wise rather than regenerated, so the data
  validation lists and the Summary sheet's COUNTIF formulas keep working.
- RFP C's path diverges from the other three at stage 8. That is contained to one branch in the
  assembly flow, not a separate pipeline.
- Native file creation by the agent is not used for deliverables. It remains available for
  throwaway working artifacts.
