"""normalize_docx and table extraction, on a synthetic document and on the RFP A input file.

RFP A is an input document, not gold data; these tests assert only structural facts visible in the
document itself (headings, table layout, IDs), never expected outcomes from the answer key.
"""

import io

import docx
import pytest

from rfp_tools.documents import count_body_elements, normalize_docx
from rfp_tools.requirements import (
    DuplicateRequirementIdError,
    RequirementCandidate,
    assign_requirement_ids,
    extract_table_requirements,
)
from tests.conftest import REPO_ROOT

RFP_A = REPO_ROOT / "data" / "RFP_Synthetic_Dataset_Pilot" / "01_Inbound_RFPs" / "RFP-A_Cedar_Valley_Water_Authority_RFP-2026-117.docx"


def _synthetic_docx() -> bytes:
    d = docx.Document()
    d.add_heading("1. Introduction", level=1)
    d.add_paragraph("Background text.")
    d.add_heading("4. Requirements", level=1)
    d.add_heading("4.1 Security", level=2)
    d.add_paragraph("The following requirements apply.")
    t = d.add_table(rows=3, cols=3)
    for r, cells in enumerate([["ID", "Requirement", "M/D"], ["SX-001", "Encrypt data at rest.", "M"], ["SX-002", "Support SSO.", "D"]]):
        for c, text in enumerate(cells):
            t.cell(r, c).text = text
    d.add_paragraph("Second paragraph.", style="List Bullet")
    other = d.add_table(rows=2, cols=2)
    other.cell(0, 0).text, other.cell(0, 1).text = "Event", "Date"
    other.cell(1, 0).text, other.cell(1, 1).text = "Due", "2026-11-20"
    d.add_heading("Appendix A - Forms", level=1)
    d.add_paragraph("Form 1")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_sections_and_locations():
    blocks = normalize_docx(_synthetic_docx())
    by_text = {b.text: b for b in blocks}
    assert by_text["Background text."].section == "1"
    assert by_text["The following requirements apply."].section == "4.1"
    assert by_text["The following requirements apply."].location == "Section 4.1, paragraph 1"
    assert by_text["Form 1"].section == "Appendix A"
    row = next(b for b in blocks if b.row_ref == "SX-001")
    assert row.location == "Section 4.1, table row SX-001"
    assert row.cells == ["SX-001", "Encrypt data at rest.", "M"]
    assert by_text["Second paragraph."].block_type == "ListItem"


def test_block_indexes_are_document_order():
    blocks = normalize_docx(_synthetic_docx())
    assert [b.index for b in blocks] == list(range(len(blocks)))


def test_every_body_element_is_a_block():
    content = _synthetic_docx()
    assert len(normalize_docx(content)) == count_body_elements(content)


def test_only_requirement_tables_are_extracted():
    reqs = extract_table_requirements(normalize_docx(_synthetic_docx()))
    assert [(r.req_id, r.mandatory) for r in reqs] == [("SX-001", "Mandatory"), ("SX-002", "Desirable")]
    assert reqs[0].location == "Section 4.1, table row SX-001"


def test_assign_keeps_buyer_ids_and_numbers_the_rest():
    cands = [
        RequirementCandidate("", "a", None, "3", "Section 3, paragraph 1", 5),
        RequirementCandidate("MC-002", "b", None, "3", "x", 7),
        RequirementCandidate("", "c", None, "3", "y", 9),
    ]
    ids = [c.req_id for c in assign_requirement_ids(cands, "MC")]
    assert ids == ["MC-001", "MC-002", "MC-003"]


def test_assign_skips_numbers_the_buyer_used():
    cands = [RequirementCandidate("MC-001", "a", None, "", "", 1), RequirementCandidate("", "b", None, "", "", 2)]
    assert [c.req_id for c in assign_requirement_ids(cands, "MC")] == ["MC-001", "MC-002"]


def test_duplicate_buyer_ids_rejected():
    cands = [RequirementCandidate("X-1", "a", None, "", "", 1), RequirementCandidate("X-1", "b", None, "", "", 2)]
    with pytest.raises(DuplicateRequirementIdError):
        assign_requirement_ids(cands, "X")


# --- RFP A structure (input document) ------------------------------------------------------

@pytest.fixture(scope="module")
def rfp_a_blocks():
    return normalize_docx(RFP_A.read_bytes())


def test_rfp_a_coverage(rfp_a_blocks):
    assert len(rfp_a_blocks) == count_body_elements(RFP_A.read_bytes())


def test_rfp_a_table_requirements(rfp_a_blocks):
    reqs = extract_table_requirements(rfp_a_blocks)
    ids = [r.req_id for r in reqs]
    assert ids == [f"CV-{n:03d}" for n in range(1, 61)]
    assert all(r.mandatory in ("Mandatory", "Desirable") for r in reqs)
    assert all(r.location.startswith("Section 4.") for r in reqs)
    assert reqs[0].location == "Section 4.1, table row CV-001"


def test_rfp_a_non_requirement_tables_ignored(rfp_a_blocks):
    # Schedule, response codes and weights tables sit in sections 2 and 3.
    reqs = extract_table_requirements(rfp_a_blocks)
    assert not any(r.section.startswith(("2", "3")) for r in reqs)
