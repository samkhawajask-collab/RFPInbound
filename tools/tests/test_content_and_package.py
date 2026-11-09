"""Company content, opportunity matching, certificate mentions, card rendering and package assembly."""

import io
import json
from datetime import date

import docx

from rfp_tools import Certification
from rfp_tools.assembly import heading1_titles
from rfp_tools.content import certifications_mentioned, company_content, match_opportunity, render_card
from rfp_tools.package import assemble_package
from tests.conftest import REPO_ROOT, read_reference_csv

DATA = REPO_ROOT / "data" / "RFP_Synthetic_Dataset_Pilot"


def test_company_content_has_text_and_scope_limits():
    files = [(p.name, p.read_bytes()) for p in (DATA / "04_Company_Content").glob("*.docx")]
    out = company_content(files)
    assert "Company_Overview.docx" in out["text"]
    assert "not included" in out["scope"].lower()


def test_match_opportunity_by_rfp_number():
    opps = [{"id": r["hrs_opportunityid"], "rfpNumber": r["hrs_rfpnumber"]} for r in read_reference_csv("06_hrs_opportunity.csv")]
    assert match_opportunity("RFP-A_Cedar_Valley_Water_Authority_RFP-2026-117.docx", opps) == "OPP-2026-117"
    assert match_opportunity("unrelated.docx", opps) is None


def test_certifications_mentioned():
    certs = [Certification("C1", "ISO 9001:2015", date(2023, 7, 1), date(2026, 6, 30)),
             Certification("C2", "ISO/IEC 27001:2022", date(2025, 2, 15), date(2028, 2, 14)),
             Certification("C3", "SOC 2 Type II (Security)", date(2025, 6, 1), date(2027, 5, 19))]
    out = certifications_mentioned([{"reqId": "X", "text": "Hold a current ISO 9001 certificate and a SOC 2 report."},
                                    {"reqId": "Y", "text": "Describe your training."}], certs, date(2026, 11, 20))
    hits = {h["certId"]: h["valid"] for h in out[0]["certifications"]}
    assert hits == {"C1": False, "C3": True}
    assert out[1]["certifications"] == []


def test_render_card_bindings_and_repeater():
    template = (DATA / "06_Templates" / "Approval_Card.json").read_text(encoding="utf-8")
    card = render_card(template, {"buyerName": "B", "rfpNumber": "N", "totalRequirements": 60,
                                  "exceptions": [{"ruleId": "R-01", "severity": "Block", "reqId": "X-1", "summary": "Cap"},
                                                 {"ruleId": "R-02", "severity": "Block", "reqId": "X-2", "summary": "SLA"}]})
    text = json.dumps(card)
    assert "${" not in text
    assert "R-01 (Block) - X-1: Cap" in text and "R-02 (Block) - X-2: SLA" in text


def test_assemble_package():
    reqs = [
        {"reqId": "X-001", "location": "Section 4.1, table row X-001", "text": "Do A.", "category": "COMP", "mandatory": "Mandatory",
         "compliance": "Full", "responseCode": "F", "responseText": "We do A.", "answerId": "ANS-1", "policyFlags": [], "status": "Drafted"},
        {"reqId": "X-002", "location": "Section 4.2, table row X-002", "text": "Unlimited liability.", "category": "COMM",
         "mandatory": "Mandatory", "compliance": "No", "responseCode": "N", "responseText": "Cannot accept.", "answerId": "ANS-2",
         "policyFlags": [{"ruleId": "R-01", "severity": "Block", "approvedPosition": "Cap at fees."}], "status": "Drafted"},
        {"reqId": "X-003", "location": "Section 4.3, table row X-003", "text": "Do C.", "category": "TECH", "mandatory": "Desirable",
         "compliance": "Gap", "policyFlags": [], "status": "SME review"},
    ]
    names = ["RFPNumber", "RFPTitle", "BuyerName", "SubmissionDate", "CoverLetterBody", "SignatoryName", "SignatoryTitle",
             "ExecutiveSummary", "CompanyQualifications", "CaseStudies", "SolutionNarrative", "ImplementationApproach",
             "ScheduleTable", "TrainingApproach", "SupportModel", "SecurityNarrative", "Company"]
    out = assemble_package(
        proposal_template=(DATA / "06_Templates" / "Proposal_Template.docx").read_bytes(),
        matrix_template=(DATA / "06_Templates" / "Compliance_Matrix_Template.xlsx").read_bytes(),
        scalars={n: f"<{n}>" for n in names}, requirements=reqs, team=[{"Name": "A", "Role": "Lead"}],
        excluded_headings=["Cover Letter", "Requirements Response Matrix", "Appendices"])
    assert out["exceptionKeys"] == [["R-01", "X-002"]]
    assert "Cover Letter" not in heading1_titles(out["governed"])
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(out["excluded"])).paragraphs) + \
        "\n".join(c.text for t in docx.Document(io.BytesIO(out["excluded"])).tables for r in t.rows for c in r.cells)
    assert "No approved library answer" in text and "No (N)" in text


def test_addendum_matches_its_opportunity_by_buyer_when_the_number_is_absent():
    from rfp_tools.content import match_opportunity
    opps = [{"id": "o-d", "rfpNumber": "THPA RFP 2026-09", "buyer": "Tri-Harbor Port Authority"},
            {"id": "o-a", "rfpNumber": "CVWA RFP 2026-117", "buyer": "Cedar Valley Water Authority"}]
    assert match_opportunity("RFP-D_Tri-Harbor_Port_Authority_RFP-2026-09_SCANNED.pdf", opps) == "o-d"
    assert match_opportunity("RFP-D_Tri-Harbor_Port_Authority_Addendum-No1.pdf", opps) == "o-d"
    assert match_opportunity("Unrelated_Notice.pdf", opps) is None


def test_forms_checklist_accounts_for_every_required_form_and_g6_checks_it():
    """Gate G6's forms condition (ARCHITECTURE.md 4.2): every required form is on the package's checklist."""
    from rfp_tools.gates import gate_g6 as check_g6
    from rfp_tools.package import INCLUDED, TO_ATTACH, forms_checklist
    headings = ["Cover Letter", "1. Executive Summary", "6. Signature Page"]
    rows = forms_checklist(["Signature Page", "W-9", "Acknowledgement of Addendum No. 1", "W-9", ""], headings)
    assert rows == [{"form": "Signature Page", "status": INCLUDED}, {"form": "W-9", "status": TO_ATTACH},
                    {"form": "Acknowledgement of Addendum No. 1", "status": TO_ATTACH}]
    accounted = [r["form"] for r in rows]
    assert check_g6(10, 20, {}, None, ["Signature Page", "W-9", "Acknowledgement of Addendum No. 1"], accounted).passed
    # An addendum that adds a form after the package was assembled is caught.
    late = check_g6(10, 20, {}, None, ["Signature Page", "Acknowledgement of Addendum No. 2"], accounted)
    assert not late.passed and "Acknowledgement of Addendum No. 2" in late.problems[0]
