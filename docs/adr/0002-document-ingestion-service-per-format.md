# ADR-0002 — Document ingestion service per format

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0003](0003-requirement-identity-and-source-location-model.md)

## Context

Four input formats with very different extraction problems:

| Format | Pilot file | Problem |
|---|---|---|
| `.docx` | RFP A — 15 pages, 60 requirements | Numbered requirement tables with buyer IDs. Structurally easy. |
| Native `.pdf` | RFP B — 16 pages, 90 requirements | No buyer IDs at all. Requirements embedded in numbered prose paragraphs, one table and three appendices with three numbering styles. One requirement spans a page break. |
| `.xlsx` | RFP C — 110 questions | Trivial structure, strict response contract (fixed columns, no row changes). |
| Scanned `.pdf` | RFP D — 7 image-only pages + 1-page native addendum, 51 requirements | ~150 dpi grayscale, JPEG-compressed, ±0.9° skew, sensor noise, punch-hole shadows, rotated RECEIVED stamp. Tesseract recovers about 94% of requirement lines verbatim. |

Every downstream stage needs the same thing from all four: ordered text blocks that each know their
page, section and row, with a confidence value.

## Options considered

### Option A — One service for everything

Send all four formats to Azure AI Document Intelligence.

- **For:** One code path, one coordinate model, one confidence model.
- **Against:** Wasteful and lossy for `.docx` and `.xlsx`. Open XML already contains the exact
  structure — heading hierarchy, table row identity, sheet/row/cell addresses. Round-tripping
  through a document-understanding model to recover what the file states explicitly loses the
  buyer's own IDs and row references, and costs 8 credits per page for nothing.

### Option B — Keep ingestion inside Power Platform

Use AI Builder text recognition or prompt document input for PDFs.

- **For:** No Azure subscription needed; stays inside one licensing and DLP boundary.
- **Against:** AI Builder returns coordinates as *percentages* of page dimensions rather than
  absolute positions, which makes page-image crops for the low-confidence verification queue harder
  and makes location claims approximate. Prompt document input caps at **50 pages and 25 MB** — the
  pilot files fit, but the 10× design volume may not. Neither exposes a per-paragraph confidence
  model as cleanly as Layout.

### Option C — Open XML for Office formats, Document Intelligence Layout for both PDF paths *(chosen)*

- **For:** Each format is handled by the tool that knows most about it. Open XML gives exact
  structure for free; Layout gives paragraphs and tables with `pageNumber` and bounding polygons
  (inches for PDF), which is what makes both page-accurate locations and image crops possible. Both
  PDF paths — native and scanned — use the **same** model and the same code, so RFP D is not a
  separate pipeline, only a lower-confidence one. S0 limits (500 MB, 2,000 pages) are far above the
  design volume.
- **Against:** Two ingestion code paths to maintain; an Azure dependency and a second cost meter;
  Document Intelligence availability is an open question for this tenant.

## Decision

**Option C.**

- `.docx` and `.xlsx` → Open XML parsing inside the `rfp-tools` service.
- Native `.pdf` and scanned `.pdf` → Azure AI Document Intelligence, **Layout** model, S0, US region.
- All four write the same `hrs_documentblock` contract: `{page, section, blockType, text, rowRef,
  polygon, confidence}`.

Everything downstream reads blocks, never files.

The scanned path additionally:

- records `hrs_rfpdocument.hrs_ocrused` and `hrs_meanconfidence`;
- raises `OCR-LOW-CONFIDENCE` for any block below the confidence threshold;
- blocks gate G6 until every flagged requirement has been verified against a page-image crop
  rendered from the bounding polygon.

**Fallback, if Q1 resolves against an Azure subscription:** AI Builder text recognition for PDFs,
with location recorded as page plus percentage coordinates, the confidence queue driven by whatever
confidence AI Builder exposes, and the extraction-recall thresholds for RFPs B and D reduced. This
is a materially weaker design and is documented as a fallback, not an equal option.

## Consequences

- RFP D's pipeline is identical to RFP B's; only the confidence distribution differs. That is why
  slice 5 is small.
- The low-confidence verification queue is a mandatory component, not a nice-to-have: at 94%
  verbatim line recovery, roughly 3 of RFP D's 51 requirements will contain a corrupted character,
  and Addendum No. 1 turns on a single digit (99.5% → 99.95%, which newly breaches rule R-02).
- Two cost meters: Copilot Credits plus Document Intelligence per page.
- `hrs_documentblock` becomes the largest runtime table and the dominant Dataverse storage cost.
- If the Azure dependency is refused, slices 3 and 5 change shape and their thresholds drop.
