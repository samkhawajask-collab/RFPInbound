"""normalize_document: a source file -> ordered blocks (ARCHITECTURE.md section 6.1).

Every format produces the same thing: an ordered stream of blocks carrying
{page, section, blockType, text, rowRef, polygon, confidence}. Downstream stages read blocks, not
files, and a requirement's location string is derived from its block, never typed.

This module handles Word (.docx) by reading the Open XML body in document order. Word has no fixed
pagination, so `page` is None for .docx and gate G1 checks body coverage instead of pages. Native PDF
is normalized from a Document Intelligence Layout result in pdf_layout.py.
"""

import io
import re
from dataclasses import asdict, dataclass, field

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

_SECTION_NUMBER = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(.+)$")
_APPENDIX = re.compile(r"^\s*Appendix\s+([A-Z])\b\s*[-:–—]?\s*(.*)$", re.IGNORECASE)


@dataclass
class Block:
    index: int
    block_type: str  # Heading | Paragraph | ListItem | TableRow (hrs_blocktype labels)
    text: str
    section: str  # "4.1", "Appendix A", or "" before the first heading
    section_title: str
    heading_path: list[str] = field(default_factory=list)
    table_index: int | None = None  # 1-based, counting every table in the document
    row_index: int | None = None  # 0 = header row
    cells: list[str] = field(default_factory=list)
    page: int | None = None
    confidence: float = 1.0
    row_ref: str = ""
    location: str = ""
    # PDF only (pdf_layout.py): every page the block covers (two when it spans a page break), its
    # bounding regions [{pageNumber, polygon}] in inches, and the buyer's own label for a numbered
    # paragraph ("3.6.2", "D.1") or a table item ("a", "C-01").
    pages: list[int] = field(default_factory=list)
    regions: list[dict] = field(default_factory=list)
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _heading_level(paragraph: Paragraph) -> int | None:
    name = paragraph.style.name if paragraph.style is not None else ""
    m = re.fullmatch(r"Heading (\d)", name)
    return int(m.group(1)) if m else None


def _parse_heading(text: str) -> tuple[str, str]:
    """'4.1 Vendor Qualifications' -> ('4.1', 'Vendor Qualifications'); 'Appendix A - Forms' -> ('Appendix A', 'Forms')."""
    if m := _SECTION_NUMBER.match(text):
        return m.group(1), m.group(2).strip()
    if m := _APPENDIX.match(text):
        return f"Appendix {m.group(1).upper()}", m.group(2).strip()
    return "", text.strip()


def _section_label(section: str) -> str:
    if not section:
        return "Front matter"
    return section if section.startswith("Appendix") else f"Section {section}"


def normalize_docx(content: bytes) -> list[Block]:
    document = docx.Document(io.BytesIO(content))
    blocks: list[Block] = []
    headings: dict[int, tuple[str, str]] = {}  # level -> (section, title)
    section, section_title = "", ""
    table_count = 0
    paragraph_in_section = 0

    def add(**kwargs) -> Block:
        block = Block(index=len(blocks), section=section, section_title=section_title,
                      heading_path=[t for _, (_, t) in sorted(headings.items())], **kwargs)
        blocks.append(block)
        return block

    for element in document.element.body.iterchildren():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(element, document)
            text = paragraph.text.strip()
            if not text:
                continue
            level = _heading_level(paragraph)
            if level is not None:
                number, title = _parse_heading(text)
                headings = {lvl: v for lvl, v in headings.items() if lvl < level}
                headings[level] = (number, title)
                # The innermost numbered heading defines the section.
                numbered = [v for _, v in sorted(headings.items()) if v[0]]
                section, section_title = numbered[-1] if numbered else ("", title)
                paragraph_in_section = 0
                b = add(block_type="Heading", text=text)
                b.location = _section_label(section)
                continue
            style = paragraph.style.name if paragraph.style is not None else ""
            paragraph_in_section += 1
            b = add(block_type="ListItem" if "List" in style else "Paragraph", text=text)
            b.location = f"{_section_label(section)}, paragraph {paragraph_in_section}"
        elif tag == "tbl":
            table_count += 1
            table = Table(element, document)
            for row_index, row in enumerate(table.rows):
                cells = [c.text.strip() for c in row.cells]
                b = add(block_type="TableRow", text=" | ".join(cells), table_index=table_count,
                        row_index=row_index, cells=cells)
                first = cells[0] if cells else ""
                if row_index > 0 and re.fullmatch(r"[A-Z]{1,6}-\d{1,4}", first):
                    b.row_ref = first
                    b.location = f"{_section_label(section)}, table row {first}"
                else:
                    b.row_ref = f"Table {table_count}, row {row_index}"
                    b.location = f"{_section_label(section)}, table {table_count} row {row_index}"
    return blocks


def count_body_elements(content: bytes) -> int:
    """Non-empty paragraphs plus table rows - what gate G1 compares the block count against for .docx."""
    document = docx.Document(io.BytesIO(content))
    total = 0
    for element in document.element.body.iterchildren():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "p" and Paragraph(element, document).text.strip():
            total += 1
        elif tag == "tbl":
            total += len(Table(element, document).rows)
    return total
