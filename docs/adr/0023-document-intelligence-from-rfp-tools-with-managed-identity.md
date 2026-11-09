# ADR-0023 — Document Intelligence is called from rfp-tools with a managed identity

- **Status:** Accepted
- **Date:** 2026-10-30
- **Deciders:** Solution architecture, with the user's approval in the Slice 3 plan
- **Related:** [ADR-0002](0002-document-ingestion-service-per-format.md), [ADR-0013](0013-rfp-tools-in-python.md), ARCHITECTURE.md §6.2 and §11

## Context

ADR-0002 routes native and scanned PDFs through Azure AI Document Intelligence, Layout model. Slice 3 is
the first to call it. Hard rule 4 forbids keys in source. ARCHITECTURE.md §11 left open, as
[UNVERIFIED], whether the Power Platform Document Intelligence connector supports managed identity.

Microsoft Learn, checked 2026-10-30:
- Layout v4.0 GA is API version `2024-11-30`.
- It returns paragraphs with roles (`pageHeader`, `pageFooter`, `pageNumber`, `sectionHeading`,
  `title`) and bounding regions with `pageNumber` and polygons (inches for PDF).
- Microsoft Entra ID auth needs a single-service resource with a custom subdomain.
- The caller needs the **Cognitive Services User** role; Owner and Contributor don't grant data-plane
  access.

## Options considered

- **A — The Power Platform connector from the flow:** this needs a key in a connection, or a managed
  identity path that's unverified here. It would also return a result the flow then has to post to
  rfp-tools anyway.
- **B — rfp-tools calls the REST API with the Function App's system-assigned identity** *(chosen)*.

## Decision

**Option B.**

- **Resource:** `kind FormRecognizer`, S0, US region (`scripts/provision-azure.ps1`).
  - It has a custom subdomain and **`disableLocalAuth=true`**, so no key works even if someone reads
    one from the portal.
  - The Function App's system identity holds **Cognitive Services User** on the resource only.
- `rfp_tools.pdf_layout.analyze_layout` posts the PDF and polls the operation. It gets its token from
  `ManagedIdentityCredential`, falling back to the Azure CLI account on a developer machine.
- **The endpoint is the Function App setting `DOCINTEL_ENDPOINT`, never a request field.** A
  caller-supplied endpoint would let a request send this app's identity token to any host.
- The Power Platform environment variable `hrs_DocIntelEndpoint` isn't used. It stays defined until the
  next schema change removes it, and `scripts/import.ps1` already leaves it out while it has no value.
- **Developer access:** `provision-azure.ps1 -AllowSignedInUser` (dev only) grants the signed-in
  account the same role, so Layout responses can be recorded as test fixtures
  (`python -m rfp_tools.pdf_layout`, trimmed to pages, paragraphs and tables).
- **Normalization:** `normalize_layout` is pure and unit-tested.
  - It drops page furniture.
  - It replaces paragraphs inside a table with the table's rows.
  - It merges a body paragraph cut by a page break (no closing punctuation, next page resumes in
    lowercase) into one block covering both pages.
  - Locations follow §6.2 with a page suffix.

## Consequences

- No secret exists for Document Intelligence anywhere: not in source, settings, connections or Key Vault.
- Gate G1 for PDF compares the pages that have at least one block with the page count.
- Microsoft Learn caps synchronous outbound requests in Power Automate at 120 seconds, so a Layout call on a larger
  document must finish within that, or it needs asynchronous start and poll operations. RFP B (16 pages) is measured in
  Slice 3. Whether a longer document fits hasn't been measured.
- Scanned PDFs (Slice 5) reuse the same call and add the confidence queue.
