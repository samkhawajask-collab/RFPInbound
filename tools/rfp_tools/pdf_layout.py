"""Native PDF ingestion through Azure AI Document Intelligence, Layout model (ADR-0002, ADR-0023).

`analyze_layout` is the only network call: it posts the PDF to the Layout model (v4.0, 2024-11-30 GA)
with a Microsoft Entra token and polls until the analysis finishes. The resource has local (key) auth
disabled, so there is no key to pass: in the Function App the token comes from its system-assigned
identity, and on a developer machine from the signed-in Azure CLI account. The endpoint is read from the
DOCINTEL_ENDPOINT app setting by the caller, never taken from a request.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from rfp_tools.documents import _parse_heading, _section_label, Block

API_VERSION = "2024-11-30"
_SCOPE = "https://cognitiveservices.azure.com/.default"

_FURNITURE = {"pageHeader", "pageFooter", "pageNumber"}
# A numbered paragraph's own label: "3.6.2  The system shall ..." or "D.1  The agreement ...". Headings
# carry one or two levels ("3.4 Reporting"); body paragraphs carry three or more, or a lettered prefix.
# A lettered label can be read with a space after the dot ("B. 4"); it is normalized to "B.4".
_PARAGRAPH_LABEL = re.compile(r"^\s*(\d+(?:\.\d+){2,}|[A-Z]\.\s?\d+)\s+(.+)$", re.DOTALL)
_TABLE_CAPTION = re.compile(r"^\s*Table\s+(\d+(?:[-.]\d+)*)\b")
# A table's first cell is an item label when it is a short token: "a", "C-01", "E-1", "1".
_ITEM_LABEL = re.compile(r"^[A-Za-z]{0,2}[-.]?\d{0,3}$")
_SENTENCE_END = re.compile(r"[.!?:;\"”')\]]\s*$")


def _token() -> str:
    from azure.identity import AzureCliCredential, ChainedTokenCredential, ManagedIdentityCredential
    return ChainedTokenCredential(ManagedIdentityCredential(), AzureCliCredential()).get_token(_SCOPE).token


class TransientError(RuntimeError):
    """Document Intelligence was throttled or briefly unavailable after every retry. The service answers 503 so
    the flow's retry policy tries again (ARCHITECTURE.md 14.4, ADR-0026); bad input stays a 400."""


_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}


def _request(method: str, url: str, token: str, body: bytes | None = None, content_type: str | None = None,
             retries: int = 3, base_delay: float = 2.0, sleep=time.sleep):
    """One HTTP call with exponential backoff on throttling and transient errors, honoring Retry-After."""
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, method=method, headers={"Authorization": f"Bearer {token}"})
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            return urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code not in _RETRY_STATUSES:
                raise
            if attempt == retries:
                raise TransientError(f"Document Intelligence returned {e.code} after {retries} retries") from e
            retry_after = e.headers.get("Retry-After") if e.headers else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else base_delay * 2 ** attempt
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == retries:
                raise TransientError(f"Document Intelligence unreachable after {retries} retries: {e}") from e
            delay = base_delay * 2 ** attempt
        sleep(delay)


def analyze_layout(content: bytes, endpoint: str, poll_seconds: float = 2.0, timeout_seconds: float = 180.0) -> dict:
    """Runs prebuilt-layout on a PDF and returns `analyzeResult`."""
    token = _token()
    url = f"{endpoint.rstrip('/')}/documentintelligence/documentModels/prebuilt-layout:analyze?api-version={API_VERSION}"
    with _request("POST", url, token, content, "application/pdf") as resp:
        operation = resp.headers["Operation-Location"]
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(poll_seconds)
        with _request("GET", operation, token) as resp:
            result = json.loads(resp.read())
        status = result.get("status")
        if status == "succeeded":
            return result["analyzeResult"]
        if status == "failed":
            # The service read the document and rejected it; retrying won't help (400, not 503).
            raise ValueError(f"Layout analysis failed: {result.get('error')}")
    raise TransientError(f"Layout analysis did not finish in {timeout_seconds:.0f} s")


def _pages(item: dict) -> list[int]:
    return sorted({r["pageNumber"] for r in item.get("boundingRegions", [])})


def _page_suffix(pages: list[int]) -> str:
    if not pages:
        return ""
    return f" (page {pages[0]})" if len(pages) == 1 else f" (pages {pages[0]}-{pages[-1]})"


def _continues(previous: Block, paragraph: dict) -> bool:
    """A body paragraph cut by a page break: the earlier part has no closing punctuation and the next page
    resumes mid-sentence (lowercase). Layout returns the two parts as separate paragraphs."""
    text = paragraph["content"].lstrip()
    return (bool(previous.pages) and min(_pages(paragraph) or [0]) > previous.pages[-1]
            and not _SENTENCE_END.search(previous.text) and text[:1].islower())


def _is_raw(result: dict) -> bool:
    """A service response keeps offsets inside `spans`; a trimmed one has flat `offset`/`length`."""
    return any("spans" in x for x in [*result.get("paragraphs", []), *result.get("tables", [])])


def normalize_layout(result: dict) -> list[Block]:
    """A Layout `analyzeResult` -> ordered blocks with sections, labels, pages and regions.

    Page furniture (headers, footers, page numbers) is dropped. Paragraphs inside a table are replaced by
    the table's rows. A paragraph split by a page break is merged back into one block covering both pages.
    Locations follow ARCHITECTURE.md section 6.2: "Section 3.6, paragraph 3.6.2 (page 9)",
    "Section 3.4, Table 3-1, item a (page 8)", "Appendix C, item C-01 (page 13)".

    Accepts the service's raw `analyzeResult` (offsets inside `spans`) or a `trim`med one (flat offsets).
    """
    if _is_raw(result):
        result = trim(result)
    tables = result.get("tables", [])
    spans = [(t["offset"], t["offset"] + t.get("length", 0)) for t in tables if t.get("offset") is not None]
    items = [("table", t["offset"], t) for t in tables if t.get("offset") is not None]
    for p in result.get("paragraphs", []):
        if p.get("role") in _FURNITURE:
            continue
        off = p.get("offset")
        if off is not None and any(a <= off < b for a, b in spans):
            continue
        items.append(("paragraph", off if off is not None else 0, p))
    items.sort(key=lambda x: x[1])
    # OCR confidence (ADR-0025): Layout scores words, not paragraphs, so a block's confidence is the lowest
    # confidence among the words its span covers. Native PDFs score every word at or near 1.
    words = sorted(((w["offset"], w.get("confidence")) for pg in result.get("pages", []) for w in pg.get("words", [])
                    if w.get("offset") is not None and w.get("confidence") is not None))

    def confidence(offset: int | None, length: int) -> float:
        if offset is None or not words:
            return 1.0
        found = [c for o, c in words if offset <= o < offset + length]
        return round(min(found), 4) if found else 1.0

    blocks: list[Block] = []
    section, section_title, heading_path = "", "", []
    paragraph_in_section = 0
    table_count = 0
    caption = ""

    def add(**kwargs) -> Block:
        block = Block(index=len(blocks), section=section, section_title=section_title, heading_path=list(heading_path), **kwargs)
        block.page = block.pages[0] if block.pages else None
        blocks.append(block)
        return block

    for kind, _, item in items:
        if kind == "paragraph":
            text = item["content"].strip()
            if not text:
                continue
            pages = _pages(item)
            if item.get("role") in ("sectionHeading", "title"):
                number, title = _parse_heading(text)
                if number:
                    section, section_title = number, title
                    level = 1 if number.startswith("Appendix") else number.count(".") + 1
                    heading_path = [*heading_path[: level - 1], title]
                    paragraph_in_section = 0
                caption = ""
                b = add(block_type="Heading", text=text, pages=pages, regions=item.get("boundingRegions", []))
                b.location = _section_label(section) + _page_suffix(pages)
                continue
            previous = blocks[-1] if blocks else None
            if previous is not None and previous.block_type == "Paragraph" and _continues(previous, item):
                previous.text = f"{previous.text} {text}"
                previous.pages = sorted({*previous.pages, *pages})
                previous.regions = [*previous.regions, *item.get("boundingRegions", [])]
                previous.location = re.sub(r" \(pages? [\d-]+\)$", "", previous.location) + _page_suffix(previous.pages)
                previous.confidence = min(previous.confidence, confidence(item.get("offset"), len(item["content"])))
                continue
            if m := _TABLE_CAPTION.match(text):
                caption = f"Table {m.group(1)}"
            b = add(block_type="Paragraph", text=text, pages=pages, regions=item.get("boundingRegions", []),
                    confidence=confidence(item.get("offset"), len(item["content"])))
            if m := _PARAGRAPH_LABEL.match(text):
                b.label = m.group(1).replace(" ", "")
                b.location = f"{_section_label(section)}, paragraph {b.label}"
            else:
                paragraph_in_section += 1
                b.location = f"{_section_label(section)}, paragraph {paragraph_in_section}"
            b.location += _page_suffix(pages)
        else:
            table_count += 1
            grid: dict[int, dict[int, str]] = {}
            for c in item.get("cells", []):
                grid.setdefault(c["rowIndex"], {})[c["columnIndex"]] = c.get("content", "").strip()
            pages = _pages(item)
            table_confidence = confidence(item.get("offset"), item.get("length", 0))
            for row_index in sorted(grid):
                cells = [grid[row_index].get(i, "") for i in range(item.get("columnCount", 0))]
                b = add(block_type="TableRow", text=" | ".join(cells), table_index=table_count, row_index=row_index,
                        cells=cells, pages=pages, regions=item.get("boundingRegions", []), confidence=table_confidence)
                first = cells[0] if cells else ""
                where = _section_label(section) + (f", {caption}" if caption else "")
                if row_index > 0 and first and _ITEM_LABEL.fullmatch(first):
                    b.label = first
                    b.row_ref = first
                    b.location = f"{where}, item {first}"
                else:
                    b.row_ref = f"Table {table_count}, row {row_index}"
                    b.location = f"{where}, table {table_count} row {row_index}"
                b.location += _page_suffix(pages)
            caption = ""
    return blocks


def trim(result: dict) -> dict:
    """The parts of `analyzeResult` normalization reads: pages (size), paragraphs and tables. Words,
    lines, spans and styles are dropped, which keeps recorded fixtures small."""
    def regions(x):
        return [{"pageNumber": r["pageNumber"], "polygon": r.get("polygon", [])} for r in x.get("boundingRegions", [])]
    return {
        "apiVersion": result.get("apiVersion"),
        # Words keep only what OCR confidence needs (ADR-0025): their text, confidence and span.
        "pages": [{"pageNumber": p["pageNumber"], "width": p.get("width"), "height": p.get("height"), "unit": p.get("unit"),
                   "angle": p.get("angle"),
                   "words": [{"content": w.get("content", ""), "confidence": w.get("confidence"),
                              "offset": (w.get("span") or {}).get("offset"), "length": (w.get("span") or {}).get("length")}
                             for w in p.get("words", [])]}
                  for p in result.get("pages", [])],
        "paragraphs": [{"role": p.get("role"), "content": p["content"], "boundingRegions": regions(p),
                        "offset": (p.get("spans") or [{}])[0].get("offset")} for p in result.get("paragraphs", [])],
        "tables": [{"rowCount": t["rowCount"], "columnCount": t["columnCount"], "boundingRegions": regions(t),
                    "offset": (t.get("spans") or [{}])[0].get("offset"),
                    "length": sum(sp.get("length", 0) for sp in t.get("spans", [])),
                    "cells": [{"rowIndex": c["rowIndex"], "columnIndex": c["columnIndex"], "kind": c.get("kind", "content"),
                               "content": c.get("content", "")} for c in t.get("cells", [])]}
                   for t in result.get("tables", [])],
    }


if __name__ == "__main__":
    # python -m rfp_tools.pdf_layout <file.pdf> <out.json> <endpoint>  (records a trimmed Layout response)
    import sys
    pdf, out, endpoint = sys.argv[1:4]
    with open(pdf, "rb") as f:
        result = trim(analyze_layout(f.read(), endpoint))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"{len(result['pages'])} pages, {len(result['paragraphs'])} paragraphs, {len(result['tables'])} tables -> {out}")
