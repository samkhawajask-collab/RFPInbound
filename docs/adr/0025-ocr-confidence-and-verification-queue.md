# ADR-0025 — OCR confidence is derived in code, and low-confidence requirements wait for a person

- **Status:** Accepted
- **Date:** 2026-10-31
- **Deciders:** Solution architecture, with the user's approval in the Slice 5 plan (Teams Adaptive Card queue)
- **Related:** [ADR-0002](0002-document-ingestion-service-per-format.md), [ADR-0003](0003-requirement-identity-and-source-location-model.md), [ADR-0020](0020-scoring-runs-complete-human-waits-by-script.md), [ADR-0023](0023-document-intelligence-from-rfp-tools-with-managed-identity.md), ARCHITECTURE.md §4.2 (G6) and §6.3

## Context

ARCHITECTURE.md §6.3 makes the OCR queue mandatory: a misread digit in RFP D (99.5% against 99.95%) flips
a compliance value and an approval level. It left three things open: where a block's confidence comes
from, what the threshold is, and how a person verifies a requirement.

Document Intelligence Layout v4 returns `pages[].words[]` with a `confidence` and a `span`, and a page
`angle`. Paragraphs carry **no** confidence
([layout model](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0),
checked 2026-10-31).

## Options considered

**Block confidence.** (a) Mean word confidence over the block. (b) **Minimum** word confidence over the
block. (c) A model judgment of the text. A single misread digit is exactly the failure that matters, and a
mean over a 20-word sentence dilutes it. A model has no view of the image. **(b) is chosen.**

**Verification surface.** (a) A Power Apps screen. (b) A **Teams Adaptive Card** per requirement, with the
page crop inline. (c) Email. The project already routes gaps and approvals as Teams cards to the pilot
recipient, and a card can carry an editable text input. **(b) is chosen** (user decision, Slice 5 plan).

**Where the run waits.** (a) Only at G6. (b) **At the end of stage 2**, plus G6 as a second guard.
Waiting only at G6 lets unverified text drive answer selection, policy and drafting, which then have to be
redone. **(b) is chosen.**

## Decision

1. **Block confidence (code, `pdf_layout.normalize_layout`).** A block's confidence is the lowest
   confidence among the words its span covers. Native PDF and Word blocks have no word scores and are 1.0.
2. **Threshold.** `OCR_CONFIDENCE_MIN = 0.90` (`tools/rfp_tools/ocr.py`). Chosen from RFP D's recorded word
   confidences: median 0.993, 1st percentile 0.873. At 0.90, **8 of RFP D's 50** base requirements are
   flagged (THPA-005, 018, 027, 028, 040, 045, 046, 048). The threshold is a constant in code, not data,
   because it is a property of the OCR service, not of the company.
3. **Scanned detection (code, `ocr.is_scanned`).** A PDF is scanned when more than half its pages have
   under 50 characters of text layer. The document row records format, `hrs_ocrused`,
   `hrs_meanconfidence` and `hrs_pagecount`.
4. **Flags.** Every requirement whose source block is below the threshold gets an `OCR-LOW-CONFIDENCE`
   flag (Warn), written by `StageOcrFlags`.
5. **Queue.** Each flag starts **RFP - Verify OCR Requirement**. It renders a crop of the page image
   around the block's polygon (`OcrCrop`: pypdf extracts the page JPEG, Pillow crops polygon in inches ×
   image dpi plus a 0.15 in margin, grayscale, at most 640 px wide, JPEG quality 70, about 8 KB). The card
   shows the crop, the requirement, its location and the OCR text in an editable box, with **Text is
   correct** and **Save my correction**.
   - A correction replaces `hrs_requirementtext`; the OCR reading stays in `hrs_originaltext`.
   - Either answer resolves the flag (`hrs_resolvedon`, `hrs_resolvedby`, and the decision appended to
     `hrs_detail`).
   - When the last OCR flag of the RFP is resolved and the RFP is waiting at stage 2, the flow sets it to
     Running and the pipeline continues at stage 3.
6. **Wait.** Stage 2 sets the RFP to *Awaiting human* before it writes the flags, so a fast answer never
   finds the run still marked Running, then stops.
7. **G6.** Any unresolved OCR flag fails G6 ("OCR text not verified: …").
8. **Scoring runs (ADR-0020).** `scripts/complete-verifications.ps1` resolves open OCR flags as
   **confirmed** and never corrects text, so no answer-key text can reach Dataverse (hard rule 1).
   Misreads stay as read; the harness's `ocr_surfacing` metric (ADR-0009 amendment 6) checks that every
   one carried a flag.

## Consequences

- Only the requirements a person must look at reach a person: 8 cards for RFP D, none for A or B.
- A misread the OCR service is confident about is not caught by this mechanism. `ocr_surfacing` measures
  exactly that risk on every RFP D run.
- The crop travels inline as a data URI. **[UNVERIFIED]** the Teams limit on inline image size inside an
  Adaptive Card; at about 8 KB (about 11 KB as base64) the crop is small, and the fallback is a link to a
  stored crop.
- A buyer ID read inside a low-confidence word still gets a flag, because the whole block is flagged
  (ADR-0003 keeps the buyer's ID as the key, so a misread ID matters).
