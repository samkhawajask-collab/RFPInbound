"""Addendum reconciliation (ADR-0010, amendment 1): an addendum is a change notice.

RFP Interpret Addendum proposes typed changes; this module keeps only what code can verify and turns it into
the ADR-0010 effects. Nothing the prompt says is trusted on its own:
- a modified, withdrawn or promoted requirement must exist in the current set; an added one must not;
- quoted requirement text must appear verbatim in the addendum;
- a new due date and page limit must appear in the addendum text;
- an item label must be one of the addendum's own item labels.
Three passes run in parallel and a change stands when a majority propose it (ADR-0021).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date

from rfp_tools.schema import choice_value

KINDS = ("modify", "withdraw", "promote", "demote", "add")
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]


def _norm(text: str) -> str:
    """Whitespace, quotes and dashes normalized, lowercase: what 'appears verbatim' compares."""
    t = (text or "").replace("“", '"').replace("”", '"').replace("’", "'").replace("–", "-")
    return " ".join(t.split()).strip(' ."').lower()


def _key(change: Mapping) -> tuple:
    return (change.get("kind"), change.get("reqId") or "", _norm(change.get("newText") or ""), change.get("mandatory") or "")


def agree(passes: Sequence[Mapping | None]) -> dict:
    """Majority over the passes (2 of 3, or both of 2): changes by (kind, reqId, text, mandatory); RFP-level
    values by equality."""
    passes = [p for p in passes if p]
    need = len(passes) // 2 + 1 if passes else 1
    votes, first = {}, {}
    for p in passes:
        for k in {_key(c) for c in p.get("changes", [])}:
            votes[k] = votes.get(k, 0) + 1
        for c in p.get("changes", []):
            first.setdefault(_key(c), c)
    out = {"changes": [first[k] for k in first if votes[k] >= need]}
    for field in ("dueDate", "pageLimit", "acknowledgementRequired"):
        values = [p.get(field) for p in passes]
        winner = next((v for v in values if values.count(v) >= need), None)
        out[field] = winner
    return out


def _date_in_text(iso: str, text: str) -> bool:
    try:
        d = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return False
    t = text.lower()
    return _MONTHS[d.month - 1] in t and re.search(rf"\b{d.day}\b", t) is not None and str(d.year) in t


def item_label(value) -> str:
    """An item label as the model may write it ('[B.1]', 'Item B.1', 'B. 1') -> 'B.1'."""
    v = re.sub(r"^\s*\[?\s*(item\s+)?", "", str(value or ""), flags=re.I)
    return re.sub(r"\s+", "", v.rstrip("] .:"))


def validate(interpretation: Mapping, addendum_text: str, known_ids: Sequence[str], item_labels: Sequence[str]) -> dict:
    """Keeps verified changes; everything else goes to `rejected` with the reason (it becomes a review flag)."""
    known, labels, text = set(known_ids), set(item_labels), _norm(addendum_text)
    kept, rejected = [], []
    for c in interpretation.get("changes", []):
        if c.get("item"):
            c = {**c, "item": item_label(c["item"])}
        kind, rid = c.get("kind"), c.get("reqId") or ""
        why = None
        if kind not in KINDS:
            why = f"unknown change kind {kind!r}"
        elif kind == "add" and (not rid or rid in known):
            why = f"added requirement {rid or '(no ID)'} is missing or already exists"
        elif kind != "add" and rid not in known:
            why = f"{rid or '(no ID)'} is not a requirement of this RFP"
        elif kind in ("modify", "add") and (not c.get("newText") or _norm(c["newText"]) not in text):
            why = "new text does not appear verbatim in the addendum"
        elif kind in ("promote", "demote") and c.get("mandatory") not in ("Mandatory", "Desirable"):
            why = "promotion without a Mandatory/Desirable value"
        elif c.get("item") and c["item"] not in labels:
            why = f"item {c['item']!r} is not an item of the addendum"
        (rejected if why else kept).append({**c, "reason": why} if why else c)
    rfp = {}
    if interpretation.get("dueDate"):
        if _date_in_text(interpretation["dueDate"], addendum_text):
            rfp["dueDate"] = interpretation["dueDate"]
        else:
            rejected.append({"kind": "dueDate", "value": interpretation["dueDate"], "reason": "date not in the addendum text"})
    if interpretation.get("pageLimit"):
        if re.search(rf"\b{int(interpretation['pageLimit'])}\b", addendum_text):
            rfp["pageLimit"] = int(interpretation["pageLimit"])
        else:
            rejected.append({"kind": "pageLimit", "value": interpretation["pageLimit"], "reason": "page limit not in the addendum text"})
    rfp["acknowledgementRequired"] = bool(interpretation.get("acknowledgementRequired"))
    return {"changes": kept, "rejected": rejected, "rfp": rfp}


def _bind(entity_set: str, row_id: str) -> str:
    return f"/{entity_set}({row_id})"


def apply(rfp_id: str, revision: int, requirements: Sequence[Mapping], validated: Mapping, item_locations: Mapping[str, str],
          required_forms: Sequence[str]) -> dict:
    """ADR-0010 effects as Dataverse writes: requirement updates, new requirement candidates (classified next
    by the stage), flags and hrs_rfp updates. `item_locations` maps an addendum item label to its location."""
    by_id = {r["hrs_reqid"]: r for r in requirements}
    updates, added, flags = [], [], []

    def flag(req_row_id: str | None, req_id: str, kind: str, detail: str):
        f = {"hrs_name": f"{kind} {req_id}"[:200], "hrs_flagtype": choice_value("hrs_flagtype", kind),
             "hrs_severity": choice_value("hrs_severity", "Info"), "hrs_detail": detail, "hrs_rfp@odata.bind": _bind("hrs_rfps", rfp_id)}
        if req_row_id:
            f["hrs_requirement@odata.bind"] = _bind("hrs_requirements", req_row_id)
        flags.append(f)

    for c in validated.get("changes", []):
        kind, rid, item = c["kind"], c.get("reqId", ""), c.get("item") or ""
        where = f"Addendum No. {revision}" + (f", item {item}" if item else "")
        row = by_id.get(rid)
        if kind == "modify":
            updates.append({"id": row["hrs_requirementid"], "reqId": rid, "fields": {"hrs_requirementtext": c["newText"].strip().strip('"')}})
            flag(row["hrs_requirementid"], rid, "ADDENDUM-CHANGE", f"{where}: text revised (prior text kept in Original Text).")
        elif kind == "withdraw":
            updates.append({"id": row["hrs_requirementid"], "reqId": rid,
                            "fields": {"hrs_status": choice_value("hrs_requirementstatus", "Withdrawn")}})
            flag(row["hrs_requirementid"], rid, "ADDENDUM-WITHDRAWN", f"{where}: requirement withdrawn; no response is drafted.")
        elif kind in ("promote", "demote"):
            updates.append({"id": row["hrs_requirementid"], "reqId": rid,
                            "fields": {"hrs_mandatory": choice_value("hrs_mandatory", c["mandatory"])}})
            flag(row["hrs_requirementid"], rid, "ADDENDUM-CHANGE", f"{where}: now {c['mandatory']}; text unchanged.")
        elif kind == "add":
            added.append({"req_id": rid, "text": c["newText"].strip().strip('"'), "mandatory": c.get("mandatory"),
                          "section": c.get("section") or "", "location": item_locations.get(item, where),
                          "introducedInRevision": revision})
    for r in validated.get("rejected", []):
        flag(None, r.get("reqId") or r.get("kind", ""), "ADDENDUM-CHANGE",
             f"Addendum No. {revision}: not applied, needs review ({r['reason']}): {json.dumps(r)[:600]}")
    rfp = validated.get("rfp", {})
    rfp_fields: dict = {"hrs_currentrevision": revision}
    if rfp.get("dueDate"):
        rfp_fields["hrs_duedate"] = rfp["dueDate"]
    if rfp.get("pageLimit"):
        rfp_fields["hrs_pagelimit"] = rfp["pageLimit"]
    forms = list(required_forms)
    ack = f"Acknowledgement of Addendum No. {revision}"
    if rfp.get("acknowledgementRequired") and ack not in forms:
        forms.append(ack)
    rfp_fields["hrs_requiredforms"] = json.dumps(forms)
    return {"updates": updates, "added": added, "flags": flags, "rfp": rfp_fields}


def added_flag_rows(rfp_id: str, revision, requirements: Sequence[Mapping], added: Sequence[Mapping]) -> dict:
    """ADDENDUM-CHANGE flags for requirements an addendum added (ADR-0010 effects table). They are raised once the
    new rows exist, so each flag can point at its requirement; `requirements` are the written rows."""
    by_req = {r["hrs_reqid"]: r for r in requirements}
    rows = []
    for a in added:
        req = by_req.get(a["req_id"])
        if req is None:
            continue
        rows.append({"hrs_name": f"ADDENDUM-CHANGE {a['req_id']}"[:200], "hrs_flagtype": choice_value("hrs_flagtype", "ADDENDUM-CHANGE"),
                     "hrs_severity": choice_value("hrs_severity", "Info"),
                     "hrs_detail": f"{a.get('location') or f'Addendum No. {revision}'}: requirement added.",
                     "hrs_rfp@odata.bind": _bind("hrs_rfps", rfp_id),
                     "hrs_requirement@odata.bind": _bind("hrs_requirements", req["hrs_requirementid"])})
    return {"rows": rows, "count": len(rows)}


def prompt_input(blocks: Sequence[Mapping]) -> dict:
    """The addendum as prompt text (one line per block, item labels in brackets), its item labels, and each
    item's location for requirements it adds."""
    lines, labels = [], []
    for b in blocks:
        label = b.get("label") or ""
        if label:
            labels.append(label)
        lines.append(f"[{label}] {b['text']}" if label else b["text"])
    return {"text": "\n".join(lines), "labels": labels}


def item_locations(blocks: Sequence[Mapping], revision: int) -> dict[str, str]:
    """'B.4' -> 'Addendum No. 1, item B.4 (page 1)' (ARCHITECTURE.md section 6.2)."""
    out = {}
    for b in blocks:
        if b.get("label"):
            page = f" (page {b['page']})" if b.get("page") else ""
            out[b["label"]] = f"Addendum No. {revision}, item {b['label']}{page}"
    return out
