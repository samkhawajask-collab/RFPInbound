"""Scanned-PDF support (ADR-0025): telling scanned from native, low-confidence requirements, page-image crops.

Everything here is code. The confidence threshold is data-free and fixed in this module, chosen from RFP D's
recorded word confidences (median 0.993, 1st percentile 0.873; ADR-0025).
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence

from pypdf import PdfReader

OCR_CONFIDENCE_MIN = 0.90  # a block whose lowest word confidence is below this needs a person to verify it
NATIVE_TEXT_MIN = 50  # characters of extractable text per page that mark a page as native, not scanned
CROP_MARGIN_IN = 0.15  # inches of context around the polygon in a verification crop


def is_scanned(pdf: bytes) -> bool:
    """True when most pages have no text layer (image-only scans). Decided in code, before any OCR."""
    pages = PdfReader(io.BytesIO(pdf)).pages
    if not pages:
        return False
    imaged = sum(1 for p in pages if len((p.extract_text() or "").strip()) < NATIVE_TEXT_MIN)
    return imaged * 2 > len(pages)


def low_confidence(requirements: Sequence[Mapping], blocks: Sequence[Mapping], threshold: float = OCR_CONFIDENCE_MIN) -> list[dict]:
    """Requirements whose source block was read below the threshold: one entry per requirement, with the
    block's confidence, page and regions for the verification card. `requirements` are the stage 2 candidates
    ({req_id, text, location, source_block_index}); `blocks` are normalized blocks as dicts."""
    by_index = {b["index"]: b for b in blocks}
    out = []
    for r in requirements:
        b = by_index.get(r["source_block_index"])
        if b is None or b.get("confidence", 1.0) >= threshold:
            continue
        out.append({"reqId": r["req_id"], "text": r["text"], "location": r["location"], "confidence": b["confidence"],
                    "page": b.get("page"), "regions": b.get("regions", [])})
    return out


CARD_CROP_MAX_WIDTH = 640  # pixels: a grayscale crop this wide stays well inside an Adaptive Card's payload


def crop(pdf: bytes, regions: Sequence[Mapping], margin_in: float = CROP_MARGIN_IN, for_card: bool = False) -> bytes:
    """A JPEG of the page image around a block's regions (first page of the block only), for the verifier.

    Polygons are in inches (Layout, PDF input); the page image's pixels per inch come from its width over the
    page width in inches.
    """
    from PIL import Image  # imported here so the native path never needs Pillow

    if not regions:
        raise ValueError("no regions to crop")
    page_no = regions[0]["pageNumber"]
    poly = [v for r in regions if r["pageNumber"] == page_no for v in r.get("polygon", [])]
    page = PdfReader(io.BytesIO(pdf)).pages[page_no - 1]
    if not page.images:
        raise ValueError(f"page {page_no} has no image to crop")
    image: Image.Image = page.images[0].image
    page_w_in, page_h_in = float(page.mediabox.width) / 72, float(page.mediabox.height) / 72
    sx, sy = image.width / page_w_in, image.height / page_h_in
    xs, ys = poly[0::2], poly[1::2]
    box = (max(0, int((min(xs) - margin_in) * sx)), max(0, int((min(ys) - margin_in) * sy)),
           min(image.width, int((max(xs) + margin_in) * sx)), min(image.height, int((max(ys) + margin_in) * sy)))
    piece = image.crop(box)
    out = io.BytesIO()
    if for_card:
        # A Teams card carries the image inline, so keep it small: grayscale, at most CARD_CROP_MAX_WIDTH wide.
        piece = piece.convert("L")
        if piece.width > CARD_CROP_MAX_WIDTH:
            piece = piece.resize((CARD_CROP_MAX_WIDTH, round(piece.height * CARD_CROP_MAX_WIDTH / piece.width)))
        piece.save(out, format="JPEG", quality=70)
    else:
        piece.convert("RGB").save(out, format="JPEG", quality=85)
    return out.getvalue()
