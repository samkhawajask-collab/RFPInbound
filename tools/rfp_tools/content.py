"""Helpers that shape source material for the flows: company content, opportunities, certificates, cards.

Kept in code so the flows stay thin: each flow lists rows, calls one operation, and writes the result.
"""

import copy
import io
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date

import docx

from rfp_tools.certifications import Certification, check_certifications
from rfp_tools.documents import Block

_SCOPE_HEADING = re.compile(r"not included|exclusion|out of scope|limitations", re.IGNORECASE)


def docx_text(content: bytes) -> list[tuple[str, str]]:
    """(style, text) for every non-empty paragraph and table row, in document order."""
    d = docx.Document(io.BytesIO(content))
    out = []
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = docx.text.paragraph.Paragraph(el, d)
            if p.text.strip():
                out.append((p.style.name if p.style is not None else "", p.text.strip()))
        elif tag == "tbl":
            for row in docx.table.Table(el, d).rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    out.append(("TableRow", " | ".join(cells)))
    return out


def company_content(files: Sequence[tuple[str, bytes]], max_chars: int = 60000) -> dict:
    """Full text of the approved company documents, plus the respondent's scope statement.

    The scope statement is every section whose heading reads like a scope limit ("What Is Not
    Included", "Exclusions") together with the first section of each document, so the out-of-scope
    judgment sees what the respondent offers and what it does not.
    """
    parts, scope = [], []
    for name, content in sorted(files):
        lines = docx_text(content)
        parts.append(f"## {name}\n" + "\n".join(t for _, t in lines))
        in_scope_section, first_heading_seen, intro = False, False, []
        for style, text in lines:
            if style.startswith("Heading"):
                if first_heading_seen and not in_scope_section and intro and not any(s.startswith(f"[{name}]") for s in scope):
                    scope.append(f"[{name}] " + " ".join(intro)[:600])
                first_heading_seen = True
                in_scope_section = bool(_SCOPE_HEADING.search(text))
                if in_scope_section:
                    scope.append(f"[{name}] {text}:")
                continue
            if in_scope_section:
                scope.append(f"- {text}")
            elif len(" ".join(intro)) < 600:
                intro.append(text)
    text = "\n\n".join(parts)
    return {"text": text[:max_chars], "scope": "\n".join(scope)[:max_chars], "truncated": len(text) > max_chars}


def facts_text(blocks: Iterable[Block], requirement_block_indexes: Iterable[int], max_chars: int = 60000) -> str:
    """The solicitation's instructions: every block except requirement-table rows, with section labels."""
    skip = set(requirement_block_indexes)
    lines = []
    for b in blocks:
        if b.index in skip:
            continue
        label = f"[{b.section}] " if b.section else ""
        lines.append(f"{label}{b.text}")
    return "\n".join(lines)[:max_chars]


def match_opportunity(file_name: str, opportunities: Sequence[Mapping[str, str]]) -> str | None:
    """The opportunity whose RFP number appears in the file name (by its trailing number group, e.g. 2026-117).

    An addendum's file name often carries the buyer but not the number ("..._Port_Authority_Addendum-No1.pdf"),
    so when no number matches, the opportunity whose buyer name appears in full (ignoring separators) wins.
    """
    name = file_name.lower()
    best = None
    for opp in opportunities:
        number = str(opp.get("rfpNumber") or "")
        tail = re.findall(r"\d[\d-]*\d|\d", number)
        if tail and tail[-1] in name:
            score = len(tail[-1])
            if best is None or score > best[0]:
                best = (score, opp["id"])
    if best:
        return best[1]
    squashed = re.sub(r"[^a-z0-9]", "", name)
    buyers = [(len(b), opp["id"]) for opp in opportunities
              if (b := re.sub(r"[^a-z0-9]", "", str(opp.get("buyer") or "").lower())) and b in squashed]
    return max(buyers)[1] if buyers else None


def _cert_pattern(name: str) -> re.Pattern:
    if m := re.search(r"\b(\d{4,5})\b", name):  # ISO 9001:2015 -> 9001, ISO/IEC 27001:2022 -> 27001
        return re.compile(rf"\b{m.group(1)}\b")
    words = re.findall(r"[A-Za-z0-9]+", name)[:2]  # SOC 2 Type II -> "SOC 2"; CSA STAR -> "CSA STAR"
    return re.compile(r"\b" + r"\W*".join(map(re.escape, words)) + r"\b", re.IGNORECASE)


def certifications_mentioned(requirements: Sequence[Mapping[str, str]], certs: Sequence[Certification],
                             submission_date: date) -> list[dict]:
    """For each requirement, the register certificates it names and whether each is valid on the submission date."""
    status = {s.cert_id: s for s in check_certifications(certs, submission_date)}
    patterns = [(c, _cert_pattern(c.name)) for c in certs]
    out = []
    for r in requirements:
        hits = [{"certId": c.cert_id, "name": c.name, "expires": c.expires.isoformat(), "valid": status[c.cert_id].valid}
                for c, p in patterns if p.search(r["text"])]
        out.append({"reqId": r["reqId"], "certifications": hits})
    return out


_BINDING = re.compile(r"\$\{([\w.]+)\}")


def render_card(template: str | dict, data: Mapping) -> dict:
    """Minimal Adaptive Card templating: ${name} bindings and "$data": "${list}" repeaters.

    Unknown bindings render as an empty string, so a card never shows a raw ${...} placeholder.
    """
    card = json.loads(template) if isinstance(template, str) else copy.deepcopy(template)

    def lookup(path: str, scopes: Sequence[Mapping]):
        for scope in scopes:
            value = scope
            for part in path.split("."):
                value = value.get(part) if isinstance(value, Mapping) else None
            if value is not None:
                return value
        return None

    def render(node, scopes):
        if isinstance(node, str):
            whole = _BINDING.fullmatch(node)
            if whole:
                value = lookup(whole.group(1), scopes)
                return value if isinstance(value, (list, dict)) else ("" if value is None else str(value))
            return _BINDING.sub(lambda m: "" if (v := lookup(m.group(1), scopes)) is None else str(v), node)
        if isinstance(node, list):
            out = []
            for item in node:
                if isinstance(item, dict) and "$data" in item:
                    items = render(item["$data"], scopes)
                    template_item = {k: v for k, v in item.items() if k != "$data"}
                    for element in items if isinstance(items, list) else []:
                        out.append(render(template_item, [element, *scopes]))
                else:
                    out.append(render(item, scopes))
            return out
        if isinstance(node, dict):
            return {k: render(v, scopes) for k, v in node.items()}
        return node

    return render(card, [data])
