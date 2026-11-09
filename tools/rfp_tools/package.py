"""assemble_package: everything stage 8 produces from one call (ADR-0008, ADR-0016).

Input is plain data the flow already holds (RFP facts, requirement rows with their responses and
flags, narrative sections, team rows); output is the governed narrative, the excluded components,
the filled compliance matrix and the exception keys gate G5 checks.
"""

import io
import re
from collections.abc import Mapping, Sequence

from openpyxl import load_workbook

from rfp_tools.assembly import fill_template, heading1_titles, split_sections
from rfp_tools.matrix import fill_compliance_matrix

GAP_TEXT = "No approved library answer; routed to a subject-matter expert."


def _exceptions(requirements: Sequence[Mapping]) -> list[dict]:
    rows, keys = [], []
    for r in requirements:
        for f in r.get("policyFlags", []):
            if f.get("severity") in ("Block", "Warn"):
                rows.append({"Rule": f["ruleId"], "Severity": f["severity"], "Requirement": r["reqId"],
                             "Buyer requirement": r["text"], "Our position": f.get("approvedPosition", "")})
                keys.append([f["ruleId"], r["reqId"]])
    return rows, keys


INCLUDED = "Included in this package"
TO_ATTACH = "Attach before submission"


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower())) - {"form", "of", "the", "a", "and", "no", "page"}


def forms_checklist(required_forms: Sequence[str], package_headings: Sequence[str]) -> list[dict]:
    """Every required form, marked Included when a section of the package carries its name (every word of the
    form's name, ignoring "form", numbers and filler, appears in one heading) and otherwise Attach before
    submission. Gate G6's forms condition checks that the package accounts for each one (ARCHITECTURE.md 4.2)."""
    rows = []
    for form in dict.fromkeys(f for f in required_forms if f and f.strip()):
        words = _words(form)
        included = bool(words) and any(words <= _words(h) for h in package_headings)
        rows.append({"form": form, "status": INCLUDED if included else TO_ATTACH})
    return rows


def _add_forms_sheet(workbook: bytes, checklist: Sequence[Mapping]) -> bytes:
    wb = load_workbook(io.BytesIO(workbook))
    ws = wb.create_sheet("Required Forms")
    ws.append(["Required form", "Status"])
    for row in checklist:
        ws.append([row["form"], row["status"]])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 60, 28
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def assemble_package(*, proposal_template: bytes, matrix_template: bytes, scalars: Mapping[str, str],
                     requirements: Sequence[Mapping], team: Sequence[Mapping[str, str]],
                     excluded_headings: Sequence[str], required_forms: Sequence[str] = ()) -> dict:
    """requirements: {reqId, location, text, category, mandatory, compliance, responseCode, responseText,
    answerId, smeAnswer, policyFlags:[{ruleId, severity, approvedPosition}], otherFlags:[str], owner, status}."""
    exceptions, exception_keys = _exceptions(requirements)
    matrix_rows = []
    for r in requirements:
        response = r.get("responseText") or r.get("smeAnswer") or (GAP_TEXT if r.get("compliance") == "Gap" else "")
        code = r.get("responseCode") or ""
        matrix_rows.append({"ReqID": r["reqId"], "RequirementText": r["text"],
                            "Compliance": f"{r.get('compliance', '')}{f' ({code})' if code else ''}",
                            "ResponseText": response, "AnswerID": r.get("answerId") or ""})
    proposal = fill_template(
        proposal_template, scalars,
        tables={"TeamTable": team or [{"Name": "", "Role": ""}],
                "ExceptionsTable": exceptions or [{"Rule": "None", "Severity": "", "Requirement": "",
                                                   "Buyer requirement": "No Block or Warn policy flags.", "Our position": ""}]},
        repeating_rows={"ReqID": matrix_rows},
    )
    governed, excluded = split_sections(proposal, excluded_headings)
    matrix = fill_compliance_matrix(matrix_template, [{
        "req_id": r["reqId"], "location": r.get("location", ""), "requirement": r["text"], "category": r.get("category", ""),
        "mandatory": r.get("mandatory", ""), "compliance": r.get("compliance", ""), "response_reference": r.get("responseCode", ""),
        "answer_id": r.get("answerId", ""),
        "policy_flags": "; ".join(f"{f['ruleId']} ({f['severity']})" for f in r.get("policyFlags", [])),
        "owner": r.get("owner", ""), "status": r.get("status", ""), "notes": "; ".join(r.get("otherFlags", [])),
    } for r in requirements], scalars.get("BuyerName", ""), scalars.get("RFPNumber", ""))
    checklist = forms_checklist(required_forms, heading1_titles(proposal))
    matrix = _add_forms_sheet(matrix, checklist)
    return {"governed": governed, "excluded": excluded, "matrix": matrix, "exceptionKeys": exception_keys, "formsChecklist": checklist}
