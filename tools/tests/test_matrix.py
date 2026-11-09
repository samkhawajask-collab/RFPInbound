"""fill_compliance_matrix keeps the template's validation lists and Summary formulas intact."""

import io

from openpyxl import load_workbook

from rfp_tools.matrix import FIRST_ROW, SHEET, fill_compliance_matrix
from tests.conftest import REPO_ROOT

TEMPLATE = REPO_ROOT / "data" / "RFP_Synthetic_Dataset_Pilot" / "06_Templates" / "Compliance_Matrix_Template.xlsx"


def _formulas(wb):
    return {(ws.title, c.coordinate): c.value for ws in wb.worksheets if ws.title != SHEET
            for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("=")}


def _validations(ws):
    return sorted((str(dv.sqref), dv.formula1) for dv in ws.data_validations.dataValidation)


def test_fill_preserves_validations_and_formulas():
    template = TEMPLATE.read_bytes()
    before = load_workbook(io.BytesIO(template))
    rows = [
        {"req_id": "X-001", "location": "Section 4.1, table row X-001", "requirement": "Encrypt data.", "category": "SEC",
         "mandatory": "Mandatory", "compliance": "Full", "answer_id": "ANS-0001", "status": "Drafted"},
        {"req_id": "X-002", "location": "Section 4.2, table row X-002", "requirement": "Hold ISO 9001.", "category": "COMP",
         "mandatory": "Mandatory", "compliance": "No", "policy_flags": "R-06 (Block)", "status": "Drafted"},
    ]
    after = load_workbook(io.BytesIO(fill_compliance_matrix(template, rows, "Example Buyer", "RFP 1")))
    ws = after[SHEET]
    assert ws.cell(row=1, column=1).value == "Compliance Matrix - Example Buyer - RFP 1"
    assert ws.cell(row=FIRST_ROW, column=1).value == "X-001"
    assert ws.cell(row=FIRST_ROW + 1, column=6).value == "No"
    assert ws.cell(row=FIRST_ROW + 2, column=1).value is None  # example row content does not linger
    assert _validations(ws) == _validations(before[SHEET])
    assert _formulas(after) == _formulas(before) and _formulas(after)
