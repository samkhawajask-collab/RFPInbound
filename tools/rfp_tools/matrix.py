"""fill_compliance_matrix: populate Compliance_Matrix_Template.xlsx cell by cell (ADR-0008).

The workbook is written, not regenerated, so the data validation lists (Mandatory, Compliance,
Status) and the Summary sheet's COUNTIF formulas keep working. Only cell values change.
"""

import io
from collections.abc import Mapping, Sequence

from openpyxl import load_workbook

SHEET = "Compliance Matrix"
FIRST_ROW = 5  # row 4 holds headers; row 5 is the template's example row, which is overwritten
COLUMNS = (
    "req_id", "location", "requirement", "category", "mandatory", "compliance",
    "response_reference", "answer_id", "policy_flags", "owner", "status", "notes",
)


def fill_compliance_matrix(template: bytes, rows: Sequence[Mapping[str, str]], buyer_name: str, rfp_number: str) -> bytes:
    wb = load_workbook(io.BytesIO(template))
    ws = wb[SHEET]
    title = ws.cell(row=1, column=1)
    if isinstance(title.value, str):
        title.value = title.value.replace("{{BuyerName}}", buyer_name).replace("{{RFPNumber}}", rfp_number)
    # Clear the example row (and anything below it) before writing.
    for r in range(FIRST_ROW, max(ws.max_row, FIRST_ROW) + 1):
        for c in range(1, len(COLUMNS) + 1):
            ws.cell(row=r, column=c).value = None
    for i, row in enumerate(rows):
        for c, key in enumerate(COLUMNS, start=1):
            value = row.get(key)
            ws.cell(row=FIRST_ROW + i, column=c).value = value if value not in ("",) else None
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
