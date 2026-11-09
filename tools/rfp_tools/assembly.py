"""Proposal and letter assembly from the supplied Word templates (ADR-0008, amended by ADR-0016).

The buyer-facing templates use {{Token}} placeholders, each inside a single run. They are filled in
place, so every style, header and section of the template survives:

- scalar tokens are replaced inside their run; a value with blank-line paragraphs becomes several
  paragraphs in the same style;
- a table row whose cells hold tokens is a repeating row, cloned once per item;
- a paragraph holding only a table token ({{ExceptionsTable}}) is replaced by a real table;
- template guidance in parentheses after a token is removed.

A filled document must contain no {{...}} token; unfilled tokens raise, so a deliverable can never
ship with a placeholder in it. The governed narrative and the excluded components are produced as
separate files (ADR-0008) by split_sections.
"""

import copy
import io
import re
from collections.abc import Iterable, Mapping, Sequence

import docx
from docx.document import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

TOKEN = re.compile(r"\{\{(\w+)\}\}")
_GUIDANCE = re.compile(r"\s*\((?:Guidance:|Every Block)[^)]*\)")


class UnfilledTokenError(ValueError):
    pass


def _iter_paragraphs(container) -> Iterable[Paragraph]:
    yield from container.paragraphs
    for table in getattr(container, "tables", []):
        for row in table.rows:
            for cell in row.cells:
                yield from _iter_paragraphs(cell)


def _all_paragraphs(doc: Document) -> Iterable[Paragraph]:
    yield from _iter_paragraphs(doc)
    for section in doc.sections:
        for part in (section.header, section.footer, section.first_page_header, section.first_page_footer):
            yield from _iter_paragraphs(part)


def _replace_in_paragraph(paragraph: Paragraph, values: Mapping[str, str]) -> None:
    for run in paragraph.runs:
        if "{{" not in run.text:
            continue
        text = TOKEN.sub(lambda m: values[m.group(1)] if m.group(1) in values else m.group(0), run.text)
        run.text = _GUIDANCE.sub("", text)
    # Multi-paragraph values: split on blank lines into sibling paragraphs with the same style.
    if "\n\n" in paragraph.text and len(paragraph.runs) == 1:
        first, *rest = [p.strip() for p in paragraph.text.split("\n\n") if p.strip()]
        paragraph.runs[0].text = first
        anchor = paragraph._p
        for text in rest:
            new_p = copy.deepcopy(paragraph._p)
            anchor.addnext(new_p)
            Paragraph(new_p, paragraph._parent).runs[0].text = text
            anchor = new_p


def _fill_repeating_rows(doc: Document, rows_by_key: Mapping[str, Sequence[Mapping[str, str]]]) -> None:
    for table in doc.tables:
        for row in list(table.rows):
            tokens = TOKEN.findall("".join(c.text for c in row.cells))
            key = next((t for t in tokens if t in rows_by_key), None)
            if key is None:
                continue
            template_tr = row._tr
            for item in rows_by_key[key]:
                new_tr = copy.deepcopy(template_tr)
                template_tr.addprevious(new_tr)
                for cell_p in new_tr.iter(docx.oxml.ns.qn("w:p")):
                    _replace_in_paragraph(Paragraph(cell_p, table._parent), {k: str(v) for k, v in item.items()})
            template_tr.getparent().remove(template_tr)


def _replace_with_table(doc: Document, paragraph: Paragraph, rows: Sequence[Mapping[str, str]]) -> None:
    headers = list(rows[0].keys()) if rows else ["(none)"]
    table = doc.add_table(rows=1, cols=len(headers))
    try:
        table.style = doc.styles["Table Grid"]
    except KeyError:
        pass
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    for item in rows:
        cells = table.add_row().cells
        for i, h in enumerate(headers):
            cells[i].text = str(item.get(h, ""))
    paragraph._p.addnext(table._tbl)
    paragraph._p.getparent().remove(paragraph._p)


def fill_template(template: bytes, scalars: Mapping[str, str], tables: Mapping[str, Sequence[Mapping[str, str]]] = None,
                  repeating_rows: Mapping[str, Sequence[Mapping[str, str]]] = None) -> bytes:
    """scalars: token -> text. tables: token -> rows for {{XTable}} paragraphs. repeating_rows: first token of a
    template row -> items whose keys are that row's tokens."""
    doc = docx.Document(io.BytesIO(template))
    tables = tables or {}
    if repeating_rows:
        _fill_repeating_rows(doc, repeating_rows)
    for paragraph in list(_iter_paragraphs(doc)):
        tokens = TOKEN.findall(paragraph.text)
        table_token = next((t for t in tokens if t in tables), None)
        if table_token:
            _replace_with_table(doc, paragraph, tables[table_token])
    for paragraph in list(_all_paragraphs(doc)):
        if "{{" in paragraph.text:
            _replace_in_paragraph(paragraph, scalars)
    leftover = sorted({t for p in _all_paragraphs(doc) for t in TOKEN.findall(p.text)})
    if leftover:
        raise UnfilledTokenError(f"Unfilled template tokens: {', '.join(leftover)}")
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def template_tokens(template: bytes) -> list[str]:
    doc = docx.Document(io.BytesIO(template))
    return sorted({t for p in _all_paragraphs(doc) for t in TOKEN.findall(p.text)})


def heading1_titles(content: bytes) -> list[str]:
    doc = docx.Document(io.BytesIO(content))
    return [p.text.strip() for p in doc.paragraphs if p.style is not None and p.style.name == "Heading 1"]


def split_sections(content: bytes, excluded_headings: Iterable[str]) -> tuple[bytes, bytes]:
    """Split a filled proposal at Heading 1 boundaries into (governed narrative, excluded components).

    The title page (everything before the first Heading 1) stays with the governed narrative; it is not
    excluded unless the RFP says so. Matching ignores leading section numbers ("10. Requirements ...").
    """
    excluded = {_bare(h) for h in excluded_headings}

    def build(want_excluded: bool) -> bytes:
        doc = docx.Document(io.BytesIO(content))
        body = doc.element.body
        section = None  # None = title page, before the first Heading 1
        for el in list(body.iterchildren()):
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "sectPr":
                continue
            if tag == "p":
                p = Paragraph(el, doc)
                if p.style is not None and p.style.name == "Heading 1":
                    section = _bare(p.text)
            is_excluded = section is not None and section in excluded
            if is_excluded != want_excluded:
                body.remove(el)
        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()

    return build(False), build(True)


def _bare(heading: str) -> str:
    return re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", heading).strip().lower()
