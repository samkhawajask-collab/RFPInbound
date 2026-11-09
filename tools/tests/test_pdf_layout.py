"""Native PDF path (ADR-0022, ADR-0023): Layout normalization, candidate sentences, majority, IDs.

The fixture is a trimmed Document Intelligence Layout response for the RFP B input PDF, recorded with
`python -m rfp_tools.pdf_layout`. It is derived from the inbound document, not from gold data.
"""

import base64
import json
import sys

import pytest

from rfp_tools.pdf_layout import normalize_layout, trim
from rfp_tools.requirements import (
    assign_requirement_ids,
    id_prefix,
    mandatory_of,
    prose_requirements,
    resolve_identified,
    sentence_candidates,
    split_sentences,
)
from rfp_tools.stages import block_rows
from tests.conftest import REPO_ROOT

FIXTURE = REPO_ROOT / "tools" / "tests" / "fixtures" / "rfp_b_layout.json"
RFP_B = REPO_ROOT / "data" / "RFP_Synthetic_Dataset_Pilot" / "01_Inbound_RFPs" / "RFP-B_Marlow_County_Health_District_RFP-26-044.pdf"


@pytest.fixture(scope="module")
def layout():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def blocks(layout):
    return normalize_layout(layout)


def by_label(blocks, label):
    return next(b for b in blocks if b.label == label)


def test_paragraph_split_by_a_page_break_is_one_block(blocks):
    b = by_label(blocks, "3.3.2")
    assert b.pages == [7, 8] and b.page == 7
    assert "record asset condition assessments using configurable rating scales" in b.text
    assert b.location == "Section 3.3, paragraph 3.3.2 (pages 7-8)"
    assert {r["pageNumber"] for r in b.regions} == {7, 8}
    # The continuation is not left behind as a block of its own.
    assert not any(x.text.startswith("assessments using") for x in blocks)


def test_three_numbering_styles_and_a_lettered_table(blocks):
    assert by_label(blocks, "C-01").location == "Appendix C, item C-01 (page 13)"
    assert by_label(blocks, "D.1").location == "Appendix D, paragraph D.1 (page 14)"
    assert by_label(blocks, "E-1").location == "Appendix E, item E-1 (page 15)"
    assert by_label(blocks, "a").location == "Section 3.4, Table 3-1, item a (page 8)"
    assert by_label(blocks, "3.6.2").location == "Section 3.6, paragraph 3.6.2 (page 9)"


def test_page_furniture_and_table_cell_paragraphs_are_dropped(blocks, layout):
    furniture = {p["content"] for p in layout["paragraphs"] if p.get("role") in ("pageHeader", "pageFooter", "pageNumber")}
    assert furniture and not any(b.text in furniture for b in blocks)
    # Table cells appear once, as rows; never again as stray paragraphs.
    assert not any(b.block_type == "Paragraph" and b.text in ("Required", "Preferred") for b in blocks)


def test_every_page_with_body_text_is_covered(blocks, layout):
    covered = {p for b in blocks for p in b.pages}
    assert covered == {p["pageNumber"] for p in layout["pages"]}


def test_split_sentences_keeps_abbreviations_and_counts_together():
    text = "The system shall log changes, e.g. edits and deletions. Notices are due within twenty-four (24) hours. Done."
    assert split_sentences(text) == ["The system shall log changes, e.g. edits and deletions.",
                                     "Notices are due within twenty-four (24) hours.", "Done."]


def test_candidates_strip_labels_and_keep_table_items_whole(blocks):
    cands = sentence_candidates(blocks)
    spanning = [c for c in cands if c["where"] == "Section 3.3, paragraph 3.3.2"]
    assert any(c["text"].endswith("using configurable rating scales.") for c in spanning)
    assert not any(c["text"].startswith("3.3.2") for c in cands)
    item = next(c for c in cands if c["where"] == "Section 3.4, Table 3-1, item d")
    assert item["priority"] == "Preferred" and item["text"].startswith("The system shall")
    assert [c["id"] for c in cands[:2]] == ["K001", "K002"]
    # Header rows and unlabeled form tables are never candidates.
    assert not any(c["text"] in ("Requirement", "Security Requirement") for c in cands)


def test_majority_of_passes_decides():
    cands = [{"id": f"K00{i}"} for i in range(1, 5)]
    got = resolve_identified(cands, [{"requirementIds": ["K001", "K002", "K009"]}, {"requirementIds": ["K001", "K003"]},
                                     {"requirementIds": ["K002", "K003"]}])
    assert [c["id"] for c in got["candidates"]] == ["K001", "K002", "K003"]
    assert got["problems"] == ["identify: unknown candidate K009"]
    two = resolve_identified(cands, [{"requirementIds": ["K001", "K002"]}, {"requirementIds": ["K001"]}])
    assert [c["id"] for c in two["candidates"]] == ["K001"]


def test_ids_in_document_order_with_the_solicitation_prefix(blocks):
    cands = sentence_candidates(blocks)
    chosen = [c for c in cands if c["where"] in ("Section 3.3, paragraph 3.3.2", "Appendix C, item C-01")]
    reqs = assign_requirement_ids(prose_requirements(chosen), id_prefix("MCHD RFP 26-044"))
    assert [r.req_id for r in reqs] == [f"MCHD-{n:03d}" for n in range(1, len(chosen) + 1)]
    assert reqs[0].location == "Section 3.3, paragraph 3.3.2 (pages 7-8)" and reqs[-1].location.startswith("Appendix C, item C-01")


@pytest.mark.parametrize("number, expected", [("MCHD RFP 26-044", "MCHD"), ("RFP MCHD 26-044", "MCHD"),
                                              ("CVWA RFP 2026-117", "CVWA"), ("PLG-VSQ-2026", "PLG"), ("", "REQ")])
def test_id_prefix(number, expected):
    assert id_prefix(number) == expected


@pytest.mark.parametrize("text, priority, expected", [
    ("The system shall export reports.", "", "Mandatory"),
    ("The agency prefers a single sign-on option.", "", "Desirable"),
    ("The system shall export reports.", "Preferred", "Desirable"),
    ("Payment will be made within 60 days.", "", None)])
def test_mandatory_from_priority_column_then_modal_words(text, priority, expected):
    assert mandatory_of(text, priority) == expected


def test_block_rows_carry_page_and_regions(blocks):
    row = block_rows([by_label(blocks, "3.3.2").to_dict()], "00000000-0000-0000-0000-000000000001")[0]
    assert row["hrs_pagenumber"] == 7 and {r["pageNumber"] for r in json.loads(row["hrs_polygon"])} == {7, 8}


def test_trim_keeps_only_what_normalization_reads():
    raw = {"apiVersion": "2024-11-30", "pages": [{"pageNumber": 1, "width": 8.5, "height": 11, "unit": "inch", "angle": 0.4, "lines": [1],
                                                "words": [{"content": "x", "confidence": 0.91, "polygon": [0, 0], "span": {"offset": 0, "length": 1}}]}],
           "paragraphs": [{"content": "x", "boundingRegions": [{"pageNumber": 1, "polygon": [0, 0]}], "spans": [{"offset": 0, "length": 1}]}],
           "tables": []}
    t = trim(raw)
    assert "lines" not in t["pages"][0] and t["paragraphs"][0]["offset"] == 0 and t["pages"][0]["angle"] == 0.4
    assert t["pages"][0]["words"] == [{"content": "x", "confidence": 0.91, "offset": 0, "length": 1}]


def test_service_candidates_and_resolve_for_a_pdf(monkeypatch, layout):
    sys.path.insert(0, str(REPO_ROOT / "tools" / "service"))
    import function_app
    from tests.test_service import call
    monkeypatch.setenv("DOCINTEL_ENDPOINT", "https://example.invalid/")
    monkeypatch.setattr(function_app, "analyze_layout", lambda content, endpoint: layout)
    pdf = base64.b64encode(RFP_B.read_bytes()).decode()
    status, out = call("requirement-candidates", {"fileName": "/Inbound/rfp.pdf", "contentBase64": pdf})
    assert status == 200 and out["count"] == len(out["candidates"]) and json.loads(out["candidatesJson"])[0]["id"] == "K001"
    picked = [c["id"] for c in out["candidates"][:5]]
    status, res = call("resolve-requirements", {"candidates": out["candidates"], "rfpNumber": "MCHD RFP 26-044",
                                                "passes": [{"requirementIds": picked}] * 2 + [{"requirementIds": []}]})
    assert status == 200 and res["gateG2"]["passed"]
    assert [r["req_id"] for r in res["requirements"]] == [f"MCHD-{n:03d}" for n in range(1, 6)]
    status, norm = call("normalize-document", {"fileName": "/Inbound/rfp.pdf", "contentBase64": pdf})
    assert status == 200 and norm["gateG1"]["passed"] and norm["blockCount"] == len(norm["blocks"])


def _raw(trimmed):
    """The service's shape: offsets inside `spans`, as analyze_layout returns them (before trim)."""
    raw = json.loads(json.dumps(trimmed))
    for p in raw["paragraphs"]:
        p["spans"] = [{"offset": p.pop("offset"), "length": len(p["content"])}]
    for t in raw["tables"]:
        t["spans"] = [{"offset": t.pop("offset"), "length": t.pop("length")}]
    return raw


def test_raw_service_response_normalizes_like_the_trimmed_one(layout):
    raw = _raw(layout)
    assert [(b.location, b.text) for b in normalize_layout(raw)] == [(b.location, b.text) for b in normalize_layout(layout)]
    assert any(b.location.startswith("Appendix C, item C-01") for b in normalize_layout(raw))


def test_service_normalizes_a_raw_layout_response(monkeypatch, layout):
    sys.path.insert(0, str(REPO_ROOT / "tools" / "service"))
    import function_app
    from tests.test_service import call
    monkeypatch.setenv("DOCINTEL_ENDPOINT", "https://example.invalid/")
    monkeypatch.setattr(function_app, "analyze_layout", lambda content, endpoint: _raw(layout))
    status, out = call("requirement-candidates", {"fileName": "/Inbound/rfp.pdf", "contentBase64": base64.b64encode(RFP_B.read_bytes()).decode()})
    assert status == 200 and any(c["where"] == "Section 3.4, Table 3-1, item a" for c in out["candidates"])


def test_a_numbered_document_takes_candidates_only_from_numbered_paragraphs_and_items(blocks):
    cands = sentence_candidates(blocks)
    assert all(not c["where"].rsplit("paragraph ", 1)[-1].isdigit() for c in cands)
    assert any(c["where"] == "Appendix D, paragraph D.2" for c in cands)
    assert any(c["where"].startswith("Appendix C, item C-0") for c in cands)


def test_a_document_without_numbered_paragraphs_keeps_every_paragraph():
    from rfp_tools.documents import Block
    blocks = [Block(index=i, block_type="Paragraph", text=f"The vendor shall do thing {i}.", section="2", section_title="Scope",
                    location=f"Section 2, paragraph {i + 1}") for i in range(3)]
    assert len(sentence_candidates(blocks)) == 3
