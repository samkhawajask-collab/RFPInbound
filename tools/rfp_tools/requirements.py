"""Requirement extraction from structured tables or prose, and requirement ID assignment (ADR-0003, ADR-0022).

Word RFPs present requirements in tables whose header row starts with an ID column and carries an
M/D column (ARCHITECTURE.md section 6.2). Where that structure exists the boundaries are
deterministic, so they are code; the prompt only classifies the category.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass

from rfp_tools.documents import Block

_MANDATORY = {"M": "Mandatory", "MANDATORY": "Mandatory", "D": "Desirable", "DESIRABLE": "Desirable"}


@dataclass
class RequirementCandidate:
    req_id: str  # the buyer's ID, or "" until assign_requirement_ids runs
    text: str
    mandatory: str | None  # "Mandatory" | "Desirable" | None when the source does not say
    section: str
    location: str
    source_block_index: int

    def to_dict(self) -> dict:
        return asdict(self)


def _header_map(header_cells: Sequence[str]) -> dict[str, int] | None:
    """Column positions for a requirements table, or None when the table is not one."""
    norm = [re.sub(r"[^a-z/]", "", c.lower()) for c in header_cells]
    if not norm or not norm[0].startswith("id"):
        return None
    columns = {"id": 0}
    for i, h in enumerate(norm):
        if h.startswith("requirement") or h in ("description", "specification"):
            columns.setdefault("text", i)
        elif h in ("m/d", "md", "mandatory", "mandatory/desirable", "type"):
            columns.setdefault("mandatory", i)
    return columns if "text" in columns else None


def extract_table_requirements(blocks: Iterable[Block]) -> list[RequirementCandidate]:
    by_table: dict[int, list[Block]] = {}
    for b in blocks:
        if b.block_type == "TableRow" and b.table_index is not None:
            by_table.setdefault(b.table_index, []).append(b)
    out: list[RequirementCandidate] = []
    for table_index in sorted(by_table):
        rows = sorted(by_table[table_index], key=lambda b: b.row_index or 0)
        columns = _header_map(rows[0].cells)
        if columns is None:
            continue
        for row in rows[1:]:
            cells = row.cells
            text = cells[columns["text"]] if columns["text"] < len(cells) else ""
            if not text:
                continue
            mandatory = None
            if "mandatory" in columns and columns["mandatory"] < len(cells):
                mandatory = _MANDATORY.get(cells[columns["mandatory"]].strip().upper())
            out.append(RequirementCandidate(
                req_id=cells[0].strip(),
                text=text,
                mandatory=mandatory,
                section=row.section,
                location=row.location,
                source_block_index=row.index,
            ))
    return out


class DuplicateRequirementIdError(ValueError):
    pass


def assign_requirement_ids(candidates: Sequence[RequirementCandidate], prefix: str) -> list[RequirementCandidate]:
    """Keep the buyer's IDs; otherwise assign <PREFIX>-NNN in document order (ADR-0003).

    Numbering skips any number the buyer already used, so assigned IDs never collide with buyer IDs.
    Raises DuplicateRequirementIdError if the buyer's own IDs repeat (gate G2).
    """
    taken = {c.req_id for c in candidates if c.req_id}
    seen: set[str] = set()
    for c in candidates:
        if c.req_id:
            if c.req_id in seen:
                raise DuplicateRequirementIdError(f"Duplicate requirement ID {c.req_id}")
            seen.add(c.req_id)
    n = 0
    result = []
    for c in sorted(candidates, key=lambda c: c.source_block_index):
        if not c.req_id:
            while True:
                n += 1
                candidate_id = f"{prefix}-{n:03d}"
                if candidate_id not in taken:
                    break
            taken.add(candidate_id)
            c = RequirementCandidate(**{**c.to_dict(), "req_id": candidate_id})
        result.append(c)
    return result


# --- Prose documents (native PDF): code segments, a prompt judges, code assigns (ADR-0022) ---------

# A sentence ends at . ! or ? followed by space and a capital or an opening quote/bracket. Abbreviations
# and enumerations that end in a period never end a sentence.
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "U.S.", "No.", "Nos.", "Mr.", "Ms.", "Dr.", "St.", "vs.", "Inc.", "Ltd.", "Co.")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"“])")
_PARAGRAPH_LABEL = re.compile(r"^\s*(?:\d+(?:\.\d+){2,}|[A-Z]\.\s?\d+)\s+")
_PAGE_SUFFIX = re.compile(r" \(pages? [\d-]+\)$")
_DESIRABLE = re.compile(r"\b(prefer(?:s|red)?|desirable|should|ideally)\b", re.IGNORECASE)
_MANDATORY_WORDS = re.compile(r"\b(shall|must|requires?|required)\b", re.IGNORECASE)
# A document with at least this many numbered body paragraphs ("3.6.2", "D.1") is treated as numbering
# its requirements; below it, every paragraph stays a candidate.
NUMBERED_DOCUMENT_MIN = 10
_PRIORITY = {"REQUIRED": "Mandatory", "MANDATORY": "Mandatory", "PREFERRED": "Desirable", "DESIRABLE": "Desirable", "OPTIONAL": "Desirable"}


def split_sentences(text: str) -> list[str]:
    parts, out = _SENTENCE_BREAK.split(" ".join(text.split())), []
    for part in parts:
        if out and out[-1].endswith(_ABBREVIATIONS):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return [p.strip() for p in out if p.strip()]


def mandatory_of(text: str, priority: str = "") -> str | None:
    """Mandatory or Desirable from the buyer's own priority column, else from the sentence's modal words;
    None when neither says."""
    if priority.strip().upper() in _PRIORITY:
        return _PRIORITY[priority.strip().upper()]
    if _DESIRABLE.search(text):
        return "Desirable"
    if _MANDATORY_WORDS.search(text):
        return "Mandatory"
    return None


def _table_columns(header: Sequence[str]) -> tuple[int | None, int | None]:
    """(requirement text column, priority column) of a table's header row."""
    norm = [h.strip().lower() for h in header]
    text = next((i for i, h in enumerate(norm) if "requirement" in h or h in ("description", "specification")), None)
    priority = next((i for i, h in enumerate(norm) if h in ("priority", "m/d", "mandatory/desirable")), None)
    return text, priority


def sentence_candidates(blocks: Sequence[Block]) -> list[dict]:
    """Every sentence a requirement could be, in document order, with its block and location.

    Paragraph blocks are split into sentences (a numbered paragraph's label is not part of the text). A
    table row with an item label is one candidate: its requirement column, or every non-label cell when no
    column is named for requirements. Headings, header rows and unlabeled table rows are not candidates,
    and neither are unnumbered paragraphs in a document that numbers its paragraphs.
    """
    out: list[dict] = []
    headers: dict[int, list[str]] = {}
    for b in blocks:
        if b.block_type == "TableRow" and b.row_index == 0 and b.table_index is not None:
            headers[b.table_index] = b.cells
    # A buyer that numbers its body paragraphs states its requirements in them (and in labeled items);
    # its unnumbered paragraphs are introductions, background and submission instructions (ADR-0022).
    numbered = sum(1 for b in blocks if b.block_type == "Paragraph" and b.label) >= NUMBERED_DOCUMENT_MIN
    for b in blocks:
        location = _PAGE_SUFFIX.sub("", b.location)
        if b.block_type in ("Paragraph", "ListItem"):
            if numbered and not b.label:
                continue
            for s in split_sentences(_PARAGRAPH_LABEL.sub("", b.text, count=1)):
                out.append({"blockIndex": b.index, "section": b.section, "location": b.location, "where": location,
                            "text": s, "priority": ""})
        elif b.block_type == "TableRow" and b.label and b.row_index:
            text_col, priority_col = _table_columns(headers.get(b.table_index, []))
            if text_col is not None and text_col < len(b.cells):
                text = b.cells[text_col]
            else:
                text = " ".join(c for c in b.cells[1:] if c)
            if not text.strip():
                continue
            priority = b.cells[priority_col] if priority_col is not None and priority_col < len(b.cells) else ""
            out.append({"blockIndex": b.index, "section": b.section, "location": b.location, "where": location,
                        "text": " ".join(text.split()), "priority": priority})
    for n, c in enumerate(out, start=1):
        c["id"] = f"K{n:03d}"
    return out


def resolve_identified(candidates: Sequence[Mapping], passes: Sequence[Mapping | Sequence[Mapping] | None]) -> dict:
    """Self-consistency (ADR-0021, ADR-0022): a candidate is a requirement when a majority of the passes of
    RFP Identify Requirements name it (2 of 3, or both of 2). Unknown IDs are reported, not used. A pass is one
    prompt output, or a list of them when the candidates were sent in batches (ADR-0029)."""
    passes = [p if isinstance(p, Mapping) else {"requirementIds": [i for b in p for i in (b or {}).get("requirementIds", [])]}
              for p in passes if p]
    known = {c["id"] for c in candidates}
    votes: dict[str, int] = {}
    problems = []
    for p in passes:
        ids = set(p.get("requirementIds", []))
        problems += [f"identify: unknown candidate {i}" for i in sorted(ids - known)]
        for i in ids & known:
            votes[i] = votes.get(i, 0) + 1
    need = len(passes) // 2 + 1 if passes else 1
    chosen = [c for c in candidates if votes.get(c["id"], 0) >= need]
    return {"candidates": chosen, "problems": problems}


def prose_requirements(candidates: Sequence[Mapping]) -> list[RequirementCandidate]:
    """Chosen candidates -> requirement candidates with no buyer ID, ready for assign_requirement_ids."""
    return [RequirementCandidate(req_id="", text=c["text"], mandatory=mandatory_of(c["text"], c.get("priority", "")),
                                 section=c["section"], location=c["location"], source_block_index=c["blockIndex"])
            for c in candidates]


def id_prefix(rfp_number: str, fallback: str = "REQ") -> str:
    """The solicitation's letters: 'RFP MCHD 26-044' -> 'MCHD', 'CVWA-2026-117' -> 'CVWA' (ADR-0003).
    A leading 'RFP', 'RFQ', 'RFI' or 'ITB' is the document type, not the buyer's prefix."""
    for token in re.findall(r"[A-Za-z]+", rfp_number or ""):
        if token.upper() not in ("RFP", "RFQ", "RFI", "ITB", "NO"):
            return token.upper()[:8]
    return fallback


# --- Requirements led by the buyer's own IDs (scanned RFP D: "THPA-036 The system must ... (M)") -------

# OCR spaces and punctuation around the hyphen vary ("THPA -007", "THPA - 043", ". THPA - 045"), so the ID
# is matched loosely and normalized. Two requirements can share one Layout paragraph, and a trailing
# "(M)"/"(D)" can land in a paragraph of its own; both are repaired here, in code (ADR-0022 amendment).
_BUYER_ID = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,6})\s*[-–]\s*(\d{2,4})(?!\d)")
_MD_MARK = re.compile(r"\(\s*([MD])\s*\)\s*[.:]?\s*$")
_LONE_MARK = re.compile(r"^\(\s*([MD])\s*\)\s*$")
BUYER_ID_DOCUMENT_MIN = 10  # a document with at least this many ID-led blocks numbers its own requirements


def _id_led(text: str) -> re.Match | None:
    """The buyer ID that leads a block (after OCR debris such as '. '), or None."""
    m = _BUYER_ID.search(text)
    return m if m and not re.sub(r"[\s.,;:·•-]", "", text[:m.start()]) else None


def labeled_requirements(blocks: Sequence[Block]) -> list[RequirementCandidate]:
    """Requirements whose blocks start with the buyer's ID, one per ID occurrence, in document order.

    Returns [] for a document that does not lead its requirements with IDs (fewer than BUYER_ID_DOCUMENT_MIN),
    so callers fall back to sentence candidates.
    """
    led = [b for b in blocks if b.block_type == "Paragraph" and _id_led(b.text)]
    if len(led) < BUYER_ID_DOCUMENT_MIN:
        return []
    out: list[RequirementCandidate] = []
    for i, b in enumerate(blocks):
        if b.block_type != "Paragraph" or not _id_led(b.text):
            continue
        matches = list(_BUYER_ID.finditer(b.text))
        # A lone "(M)" or "(D)" in the next block belongs to this block's last requirement.
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        orphan = _LONE_MARK.match(nxt.text) if nxt is not None and nxt.block_type == "Paragraph" else None
        for k, m in enumerate(matches):
            end = matches[k + 1].start() if k + 1 < len(matches) else len(b.text)
            body = b.text[m.end():end].strip(" .:-")
            mark = _MD_MARK.search(body)
            letter = mark.group(1) if mark else (orphan.group(1) if orphan and k == len(matches) - 1 else None)
            text = " ".join(_MD_MARK.sub("", body).split()).rstrip(" .,;:")
            if text:
                text += "."
            req_id = f"{m.group(1)}-{m.group(2)}"
            base = _PAGE_SUFFIX.sub("", b.location)
            where = re.sub(r", paragraph \S+$", "", base)
            out.append(RequirementCandidate(
                req_id=req_id, text=text, mandatory={"M": "Mandatory", "D": "Desirable"}.get(letter),
                section=b.section, location=f"{where}, requirement {req_id}{b.location[len(base):]}",
                source_block_index=b.index))
    return out
