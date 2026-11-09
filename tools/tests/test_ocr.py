"""Scanned path (ADR-0025, ADR-0022 amendment): block confidence, buyer-ID requirements, scanned detection, crops.

Fixtures are trimmed Layout responses for RFP D's inbound documents, recorded with
`python -m rfp_tools.pdf_layout`. They are derived from the inputs, not from gold data.
"""

import io
import json

import pytest

from rfp_tools import ocr
from rfp_tools.pdf_layout import normalize_layout
from rfp_tools.requirements import labeled_requirements, sentence_candidates
from tests.conftest import REPO_ROOT

FIXTURES = REPO_ROOT / "tools" / "tests" / "fixtures"
INBOUND = REPO_ROOT / "data" / "RFP_Synthetic_Dataset_Pilot" / "01_Inbound_RFPs"
SCANNED = INBOUND / "RFP-D_Tri-Harbor_Port_Authority_RFP-2026-09_SCANNED.pdf"


@pytest.fixture(scope="module")
def d_blocks():
    return normalize_layout(json.loads((FIXTURES / "rfp_d_layout.json").read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def d_requirements(d_blocks):
    return labeled_requirements(d_blocks)


def test_block_confidence_is_the_lowest_word_confidence(d_blocks):
    noisy = next(b for b in d_blocks if b.text.startswith("THPA-005"))
    assert noisy.confidence < 0.2
    clean = next(b for b in d_blocks if b.text.startswith("THPA-036"))
    assert clean.confidence > 0.95
    native = normalize_layout(json.loads((FIXTURES / "rfp_b_layout.json").read_text(encoding="utf-8")))
    assert all(b.confidence == 1.0 for b in native)  # no word scores recorded: a native block is fully confident


def test_every_buyer_id_is_found_once_despite_ocr_noise(d_requirements):
    ids = [r.req_id for r in d_requirements]
    assert ids == [f"THPA-{n:03d}" for n in range(1, 51)]  # "THPA -007", "THPA - 043", ". THPA - 045" normalized


def test_merged_paragraphs_split_and_orphan_marks_reattached(d_requirements):
    by = {r.req_id: r for r in d_requirements}
    # 019/020 and 045/046 share a Layout paragraph; each still gets its own text and marker.
    assert by["THPA-019"].text.endswith("with the CIS.") and by["THPA-020"].text.startswith("The Vendor must describe API")
    assert by["THPA-019"].source_block_index == by["THPA-020"].source_block_index
    # "(D)" and "(M)" on lines of their own belong to 034 and 050.
    assert by["THPA-034"].mandatory == "Desirable" and by["THPA-050"].mandatory == "Mandatory"
    assert all(r.mandatory for r in d_requirements)
    assert not any("(M)" in r.text or "(D)" in r.text or r.text.endswith(",.") for r in d_requirements)


def test_locations_name_the_section_requirement_and_page(d_requirements):
    by = {r.req_id: r for r in d_requirements}
    assert by["THPA-036"].location == "Section 5.9, requirement THPA-036 (page 5)"
    assert by["THPA-036"].text == "The system must be available 99.5% of the time, measured monthly."


def test_documents_without_id_led_requirements_fall_back_to_candidates():
    b = normalize_layout(json.loads((FIXTURES / "rfp_b_layout.json").read_text(encoding="utf-8")))
    assert labeled_requirements(b) == [] and sentence_candidates(b)


def test_scanned_detection_by_text_layer():
    assert ocr.is_scanned(SCANNED.read_bytes())
    assert not ocr.is_scanned((INBOUND / "RFP-D_Tri-Harbor_Port_Authority_Addendum-No1.pdf").read_bytes())
    assert not ocr.is_scanned((INBOUND / "RFP-B_Marlow_County_Health_District_RFP-26-044.pdf").read_bytes())


def test_low_confidence_requirements_are_flagged_with_their_page_and_regions(d_blocks, d_requirements):
    flagged = ocr.low_confidence([r.to_dict() for r in d_requirements], [b.to_dict() for b in d_blocks])
    assert {f["reqId"] for f in flagged} == {"THPA-005", "THPA-018", "THPA-027", "THPA-028", "THPA-040",
                                              "THPA-045", "THPA-046", "THPA-048"}
    assert all(f["confidence"] < ocr.OCR_CONFIDENCE_MIN and f["page"] and f["regions"] for f in flagged)


def test_crop_frames_the_block_on_the_page_image(d_blocks):
    from PIL import Image
    b = next(x for x in d_blocks if x.text.startswith("THPA-005"))
    img = Image.open(io.BytesIO(ocr.crop(SCANNED.read_bytes(), b.regions)))
    # 150 dpi page image: a two-line paragraph about 6.4 in wide, plus a 0.15 in margin each side.
    assert 900 <= img.width <= 1050 and 80 <= img.height <= 140
    with pytest.raises(ValueError):
        ocr.crop(SCANNED.read_bytes(), [])


def _service(monkeypatch, layout_file):
    import sys
    sys.path.insert(0, str(REPO_ROOT / "tools" / "service"))
    import function_app
    from tests.test_service import call
    monkeypatch.setenv("DOCINTEL_ENDPOINT", "https://example.invalid/")
    layout = json.loads((FIXTURES / layout_file).read_text(encoding="utf-8"))
    monkeypatch.setattr(function_app, "analyze_layout", lambda content, endpoint: layout)
    return call


def test_service_takes_buyer_ids_by_code_and_reports_low_confidence(monkeypatch):
    import base64
    call = _service(monkeypatch, "rfp_d_layout.json")
    pdf = base64.b64encode(SCANNED.read_bytes()).decode()
    status, norm = call("normalize-document", {"fileName": "/Inbound/scan.pdf", "contentBase64": pdf})
    assert status == 200 and norm["format"] == "PdfScanned" and norm["ocrUsed"] and norm["pageCount"] == 7
    assert 0.5 < norm["meanConfidence"] < 1.0 and norm["gateG1"]["passed"]
    status, out = call("requirement-candidates", {"fileName": "/Inbound/scan.pdf", "contentBase64": pdf, "expectedMin": 1, "expectedMax": 1000})
    assert status == 200 and out["mode"] == "labeled" and out["count"] == 0 and out["gateG2"]["passed"]
    assert len(out["requirements"]) == 50 and len(out["lowConfidence"]) == 8
    status, crop = call("ocr-crop", {"contentBase64": pdf, "regions": json.dumps(out["lowConfidence"][0]["regions"])})
    assert status == 200 and len(crop["jpegBase64"]) < 20000


def test_ocr_flags_gate_g6_and_revision_on_rows():
    from rfp_tools.gates import gate_g6
    from rfp_tools.schema import choice_label
    from rfp_tools.stages import ocr_flag_rows
    rows = ocr_flag_rows("00000000-0000-0000-0000-0000000000aa",
                         [{"hrs_reqid": "THPA-005", "hrs_requirementid": "00000000-0000-0000-0000-000000000005"}],
                         [{"reqId": "THPA-005", "confidence": 0.164, "page": 3}, {"reqId": "THPA-999", "confidence": 0.1, "page": 1}])
    assert rows["count"] == 1 and choice_label("hrs_flagtype", rows["rows"][0]["hrs_flagtype"]) == "OCR-LOW-CONFIDENCE"
    assert "0.16" in rows["rows"][0]["hrs_detail"]
    g = gate_g6(5, 20, {}, None, [], [], unresolved_ocr=["THPA-005"])
    assert not g.passed and "OCR text not verified: THPA-005" in g.problems
    assert gate_g6(5, 20, {}, None, [], []).passed
