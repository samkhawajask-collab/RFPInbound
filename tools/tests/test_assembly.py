"""fill_template / split_sections on the supplied (input) templates."""

import io

import docx
import pytest

from rfp_tools.assembly import UnfilledTokenError, fill_template, heading1_titles, split_sections, template_tokens
from tests.conftest import REPO_ROOT

TEMPLATES = REPO_ROOT / "data" / "RFP_Synthetic_Dataset_Pilot" / "06_Templates"
PROPOSAL = (TEMPLATES / "Proposal_Template.docx").read_bytes()
LETTER = (TEMPLATES / "Clarification_Letter_Template.docx").read_bytes()

MATRIX_TOKENS = {"ReqID", "RequirementText", "Compliance", "ResponseText", "AnswerID"}
TABLE_TOKENS = {"ScheduleTable", "TeamTable", "ExceptionsTable"}


def _scalars():
    names = set(template_tokens(PROPOSAL)) - MATRIX_TOKENS - TABLE_TOKENS
    values = {n: f"<{n}>" for n in names}
    values["ExecutiveSummary"] = "First paragraph.\n\nSecond paragraph."
    return values


def _filled() -> bytes:
    return fill_template(
        PROPOSAL,
        _scalars(),
        tables={"ScheduleTable": [{"Phase": "Discovery", "Weeks": "1-4"}],
                "TeamTable": [{"Name": "A. Person", "Role": "Lead"}],
                "ExceptionsTable": [{"Rule": "R-01", "Severity": "Block", "Requirement": "X-001", "Approved position": "Cap"}]},
        repeating_rows={"ReqID": [
            {"ReqID": "X-001", "RequirementText": "Do A.", "Compliance": "Full", "ResponseText": "We do A.", "AnswerID": "ANS-1"},
            {"ReqID": "X-002", "RequirementText": "Do B.", "Compliance": "No", "ResponseText": "We do not.", "AnswerID": "ANS-2"},
        ]},
    )


def _text(content: bytes) -> str:
    d = docx.Document(io.BytesIO(content))
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for r in t.rows:
            parts.append(" | ".join(c.text for c in r.cells))
    for s in d.sections:
        parts += [p.text for p in s.header.paragraphs]
    return "\n".join(parts)


def test_template_tokens_found():
    assert MATRIX_TOKENS | TABLE_TOKENS <= set(template_tokens(PROPOSAL))


def test_fill_leaves_no_tokens_and_keeps_headings():
    out = _filled()
    text = _text(out)
    assert "{{" not in text
    assert "X-001 | Do A. | Full | We do A. | ANS-1" in text and "X-002" in text
    assert "R-01 | Block | X-001 | Cap" in text
    assert "(Guidance:" not in text and "(Every Block" not in text
    assert heading1_titles(out) == heading1_titles(PROPOSAL)
    assert "<BuyerName>" in text  # header tokens filled too


def test_multi_paragraph_value_becomes_paragraphs():
    paras = [p.text for p in docx.Document(io.BytesIO(_filled())).paragraphs]
    i = paras.index("First paragraph.")
    assert paras[i + 1] == "Second paragraph."


def test_unfilled_token_raises():
    with pytest.raises(UnfilledTokenError):
        fill_template(PROPOSAL, {"BuyerName": "x"})


def test_split_governed_and_excluded():
    governed, excluded = split_sections(_filled(), ["Cover Letter", "Requirements Response Matrix", "Appendices"])
    g, e = heading1_titles(governed), heading1_titles(excluded)
    assert "Cover Letter" not in g and "10. Requirements Response Matrix" not in g and "Appendices" not in g
    assert "1. Executive Summary" in g and "9. Exceptions to Terms and Conditions" in g
    assert e == ["Cover Letter", "10. Requirements Response Matrix", "Appendices"]
    assert "We do A." not in _text(governed) and "We do A." in _text(excluded)  # matrix rows are excluded
    assert "R-01 | Block | X-001" in _text(governed)  # the Exceptions section is governed
    assert "Prepared for <BuyerName>" in _text(governed)  # title page stays with the governed narrative


def test_clarification_letter_rows():
    names = set(template_tokens(LETTER)) - {"n", "ReqIDs", "Section", "QuestionText"}
    out = fill_template(LETTER, {n: f"<{n}>" for n in names}, repeating_rows={"n": [
        {"n": "1", "ReqIDs": "X-037, X-039", "Section": "4.6", "QuestionText": "Which hosting model applies?"},
        {"n": "2", "ReqIDs": "X-038", "Section": "4.6", "QuestionText": "What does 'timely' mean?"},
    ]})
    text = _text(out)
    assert "1 | X-037, X-039 / 4.6 | Which hosting model applies?" in text
    assert "{{" not in text
