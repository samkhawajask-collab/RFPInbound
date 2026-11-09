"""Per-stage data shaping for the pipeline flows (ARCHITECTURE.md section 4).

Each function takes rows exactly as the Dataverse connector's "List rows" returns them (lookups as
_x_value, expanded lookups as nested objects, choices as integers) plus prompt output, and returns the
rows to write, with lookups expressed as @odata.bind strings. The flows stay a fixed shape - list,
call, prompt, call, write - and every join, validation and choice mapping happens here, under test.

Prompt output is trusted for judgment only. Every function checks bookkeeping (each expected ID once,
no invented IDs, codes from the allowed set) and returns problems instead of guessing.
"""

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date

from rfp_tools.certifications import Certification
from rfp_tools.compliance import derive_compliance
from rfp_tools.content import certifications_mentioned
from rfp_tools.documents import Block
from rfp_tools.library import Answer, annotate_answer, map_response_code, related_unusable, select_answer_candidates
from rfp_tools.model import FiredRule, Severity
from rfp_tools.routing import route_sme
from rfp_tools.rules import match_rule_candidates
from rfp_tools.schema import choice_label, choice_value
from rfp_tools.thresholds import Threshold, evaluate_thresholds
from rfp_tools.words import count_words


def _bind(entity_set: str, row_id: str) -> str:
    return f"{entity_set}({row_id})"


def _key_bind(entity_set: str, column: str, value: str) -> str:
    return f"{entity_set}({column}='{value.replace(chr(39), chr(39) * 2)}')"


def _expanded(row: Mapping, nav: str, column: str):
    nested = row.get(nav)
    return nested.get(column) if isinstance(nested, Mapping) else None


def _date(value) -> date | None:
    return date.fromisoformat(str(value)[:10]) if value else None


def _check_ids(expected: Sequence[str], got: Sequence[str], what: str) -> list[str]:
    problems = []
    missing = sorted(set(expected) - set(got))
    extra = sorted(set(got) - set(expected))
    dupes = sorted({g for g in got if list(got).count(g) > 1})
    if missing:
        problems.append(f"{what}: no result for {', '.join(missing)}")
    if extra:
        problems.append(f"{what}: unknown IDs {', '.join(extra)}")
    if dupes:
        problems.append(f"{what}: repeated IDs {', '.join(dupes)}")
    return problems


# --- Stage 1 ----------------------------------------------------------------------------------

def block_rows(blocks: Sequence[Mapping], document_id: str) -> list[dict]:
    rows = []
    for b in blocks:
        rows.append({
            "hrs_name": f"{b['index']:04d} {b.get('location') or b.get('section') or ''}"[:200],
            "hrs_blockindex": b["index"],
            "hrs_section": (b.get("section") or "")[:1000],
            "hrs_blocktype": choice_value("hrs_blocktype", b["block_type"]),
            "hrs_text": b["text"],
            "hrs_rowref": (b.get("row_ref") or "")[:100],
            "hrs_confidence": b.get("confidence", 1.0),
            # PDF blocks carry their page and bounding regions (the OCR verification crop uses them); Word has
            # no fixed pages, so both stay empty there and every row keeps the same shape.
            "hrs_pagenumber": b.get("page"),
            "hrs_polygon": json.dumps(b["regions"]) if b.get("regions") else None,
            "hrs_rfpdocument@odata.bind": _bind("hrs_rfpdocuments", document_id),
        })
    return rows


_ISO_DATE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")


def _iso_date(value, fallback):
    """The YYYY-MM-DD in a prompt's date string ("2026-11-02 17:00 ET" -> "2026-11-02"); dates are code, not model output."""
    m = _ISO_DATE.search(str(value or ""))
    if m:
        return m.group(1)
    m = _ISO_DATE.search(str(fallback or ""))
    return m.group(1) if m else None


def rfp_facts_update(facts: Mapping, opportunity: Mapping) -> dict:
    """hrs_rfp columns from the facts prompt, falling back to the opportunity's dates when the RFP states none."""
    codes = facts.get("responseCodes") or []
    return {
        "hrs_duedate": _iso_date(facts.get("dueDate"), opportunity.get("hrs_duedate")),
        "hrs_qadeadline": _iso_date(facts.get("qaDeadline"), opportunity.get("hrs_qadeadline")),
        "hrs_submissionmethod": (facts.get("submissionMethod") or "")[:1000],
        "hrs_pagelimit": int(facts.get("pageLimit") or 0) or None,
        "hrs_pagelimitbasis": json.dumps({
            "dueTime": facts.get("dueTime", ""),
            "pageLimitExcludes": facts.get("pageLimitExcludes", []),
            "excludedTemplateSections": facts.get("excludedTemplateSections", []),
            "responseCodes": codes,
        })[:4000],
        "hrs_wordlimitperanswer": int(facts.get("wordLimitPerAnswer") or 0) or None,
        "hrs_responsecodes": ",".join(c["code"] for c in codes)[:400],
        "hrs_evaluationweights": json.dumps(facts.get("evaluationWeights", [])),
        "hrs_requiredforms": json.dumps(facts.get("requiredForms", [])),
    }


# --- Stage 2 ----------------------------------------------------------------------------------

# Batch sizes for the prompts that saw the whole set in one call until Slice 7 (ADR-0029). Each is at least the
# largest input the pilot has run through one call, so RFPs A, B and D still make one call each and only a larger
# RFP is split; the flow loops over the batches and passes the outputs back as a list, merged here.
CLASSIFY_BATCH = 90     # RFP Classify Requirements: RFP B has 90 requirements
IDENTIFY_BATCH = 120    # RFP Identify Requirements: RFP B has 119 candidate sentences
RELATIONS_BATCH = 100   # RFP Judge Relations: RFP B has 92 requirements
SCREEN_BATCH = 100      # RFP Screen Rules: RFP B screens 92 requirements
ADJUDICATE_BATCH = 20   # RFP Adjudicate Rules: RFP B has 17 candidate hits
CLARIFY_BATCH = 5       # RFP Draft Clarifications: the pilot RFPs have 2 or 3 items


def classify_batches(requirements: Sequence[Mapping]) -> dict:
    """Extracted requirements -> RFP Classify Requirements inputs ({reqId, section, text}), in batches."""
    shape = [{"reqId": r["req_id"], "section": r.get("section") or "", "text": r["text"]} for r in requirements]
    return {"batches": [json.dumps(shape[i:i + CLASSIFY_BATCH], ensure_ascii=False, separators=(",", ":"))
                        for i in range(0, len(shape), CLASSIFY_BATCH)], "count": len(shape)}


def requirement_rows(rfp_id: str, document_id: str, requirements: Sequence[Mapping], classification: Mapping | Sequence[Mapping],
                     categories: Sequence[Mapping], revision: int = 0) -> dict:
    """classification: one RFP Classify Requirements output, or a list of them (one per batch)."""
    codes = {c["hrs_categorycode"] for c in categories}
    items = _merged(classification)["items"]
    problems = _check_ids([r["req_id"] for r in requirements], [i["reqId"] for i in items], "classification")
    problems += [f"classification: {i['reqId']} has unknown category {i['categoryCode']!r}" for i in items if i["categoryCode"] not in codes]
    by_id = {i["reqId"]: i for i in items}
    rows = []
    for r in requirements:
        c = by_id.get(r["req_id"])
        if not c or c["categoryCode"] not in codes:
            continue
        row = {
            "hrs_reqid": r["req_id"],
            "hrs_rfp@odata.bind": _bind("hrs_rfps", rfp_id),
            "hrs_location": r["location"][:400],
            "hrs_section": (r.get("section") or "")[:1000],
            "hrs_requirementtext": r["text"],
            "hrs_originaltext": r["text"],
            "hrs_categorycode@odata.bind": _key_bind("hrs_categories", "hrs_categorycode", c["categoryCode"]),
            "hrs_status": choice_value("hrs_requirementstatus", "Active"),
            "hrs_extractionconfidence": round(float(c.get("confidence", 0)), 4),
            "hrs_introducedinrevision": int(revision or 0),
            "hrs_outofscope": False,
            "hrs_ambiguous": False,
            # A lookup inside an alternate key is addressed by its _<name>_value property.
            "hrs_sourceblock@odata.bind": f"hrs_documentblocks(_hrs_rfpdocument_value={document_id},hrs_blockindex={r['source_block_index']})",
        }
        if r.get("mandatory"):
            row["hrs_mandatory"] = choice_value("hrs_mandatory", r["mandatory"])
        rows.append(row)
    return {"rows": rows, "problems": problems}


def taxonomy_text(categories: Sequence[Mapping]) -> str:
    return "\n".join(f"{c['hrs_categorycode']}: {c.get('hrs_name', '')} - {c.get('hrs_description', '')}"
                     for c in sorted(categories, key=lambda c: c["hrs_categorycode"]))


def prompt_requirements(requirements: Sequence[Mapping]) -> str:
    """Requirement rows -> compact JSON for prompts ({reqId, section, text})."""
    return json.dumps([{"reqId": r["hrs_reqid"], "section": r.get("hrs_section") or "", "text": r["hrs_requirementtext"]}
                       for r in sorted(requirements, key=lambda r: r["hrs_reqid"])], ensure_ascii=False)


def ocr_flag_rows(rfp_id: str, requirements: Sequence[Mapping], low_confidence: Sequence[Mapping]) -> dict:
    """OCR-LOW-CONFIDENCE flags (ADR-0025) for the requirements read below the threshold. `requirements` are the
    written rows (hrs_requirementid, hrs_reqid); each flag starts the verification card flow when created."""
    by_req = {r["hrs_reqid"]: r for r in requirements}
    rows = []
    for item in low_confidence:
        req = by_req.get(item["reqId"])
        if req is None:
            continue
        rows.append({"hrs_name": f"OCR-LOW-CONFIDENCE {item['reqId']}"[:200],
                     "hrs_flagtype": choice_value("hrs_flagtype", "OCR-LOW-CONFIDENCE"),
                     "hrs_severity": choice_value("hrs_severity", "Warn"),
                     "hrs_detail": f"Read at confidence {item['confidence']:.2f} (page {item.get('page')}); verify against the page image "
                                   f"before the requirement is answered.",
                     "hrs_rfp@odata.bind": _bind("hrs_rfps", rfp_id),
                     "hrs_requirement@odata.bind": _bind("hrs_requirements", req["hrs_requirementid"])})
    return {"rows": rows, "count": len(rows)}


# --- Stage 4 ----------------------------------------------------------------------------------

def agree_relations(first: Mapping, second: Mapping | None, third: Mapping | None = None) -> dict:
    """Self-consistency (ADR-0021): with three passes of RFP Judge Relations, keep a finding at least two
    report (majority); with two, keep only what both report. Reasons come from the first pass that reports
    the finding. A contradiction keeps the members a majority places in a contradiction, in the groups of
    the first pass that has them; relation_updates drops groups left with fewer than two."""
    passes = [r for r in (first, second, third) if r]
    if len(passes) < 2:
        return dict(first)
    need = 2  # both of two passes, or a majority of three

    def keep(kind: str, key) -> list:
        votes, found = {}, {}
        for r in passes:
            for k in {key(item) for item in r.get(kind, [])}:
                votes[k] = votes.get(k, 0) + 1
            for item in r.get(kind, []):
                found.setdefault(key(item), item)
        return [item for k, item in found.items() if votes[k] >= need]

    member_votes = {}
    for r in passes:
        for i in {i for c in r.get("contradictions", []) for i in c.get("reqIds", [])}:
            member_votes[i] = member_votes.get(i, 0) + 1
    members = {i for i, v in member_votes.items() if v >= need}
    groups, placed = [], set()
    for r in passes:
        for c in r.get("contradictions", []):
            ids = [i for i in c.get("reqIds", []) if i in members and i not in placed]
            if ids:
                groups.append({**c, "reqIds": ids})
                placed.update(ids)
    return {"duplicates": keep("duplicates", lambda d: (d.get("reqId"), d.get("duplicateOf"))),
            "contradictions": groups,
            "ambiguous": keep("ambiguous", lambda a: a.get("reqId")),
            "outOfScope": keep("outOfScope", lambda o: o.get("reqId"))}


def relation_batches(requirements: Sequence[Mapping]) -> dict:
    """RFP Judge Relations inputs. Up to RELATIONS_BATCH requirements go in one call, as before Slice 7; a larger set
    is grouped by category (duplicates and contradictions sit mostly within one) and packed into batches of at most
    RELATIONS_BATCH. Relations across batches are not looked for at that size (ADR-0029)."""
    rows = sorted(requirements, key=lambda r: r["hrs_reqid"])
    if len(rows) <= RELATIONS_BATCH:
        return {"batches": [prompt_requirements(rows)], "count": len(rows)}
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(_expanded(r, "hrs_categorycode", "hrs_categorycode") or "", []).append(r)
    batches, current = [], []
    for _, group in sorted(groups.items(), key=lambda g: -len(g[1])):
        for i in range(0, len(group), RELATIONS_BATCH):
            part = group[i:i + RELATIONS_BATCH]
            if len(current) + len(part) > RELATIONS_BATCH:
                batches.append(current)
                current = []
            current += part
    if current:
        batches.append(current)
    return {"batches": [prompt_requirements(b) for b in batches], "count": len(rows)}


RELATION_KINDS = ("duplicates", "contradictions", "ambiguous", "outOfScope")


def _merged_relations(outputs: Mapping | Sequence[Mapping] | None) -> Mapping | None:
    """One RFP Judge Relations output, or a list of them (one per batch) -> one output."""
    if outputs is None or isinstance(outputs, Mapping):
        return outputs
    return {k: [x for o in outputs for x in (o or {}).get(k, [])] for k in RELATION_KINDS}


def relation_updates(rfp_id: str, requirements: Sequence[Mapping], relations: Mapping | Sequence[Mapping],
                     relations2: Mapping | Sequence[Mapping] | None = None, relations3: Mapping | Sequence[Mapping] | None = None) -> dict:
    """Each pass: one RFP Judge Relations output, or a list of them (one per batch)."""
    relations = agree_relations(_merged_relations(relations), _merged_relations(relations2), _merged_relations(relations3))
    by_req = {r["hrs_reqid"]: r for r in requirements}
    problems, warnings, updates, flags = [], [], {}, []

    def known(req_id: str, what: str) -> bool:
        # A finding that names something other than a requirement of this RFP (at 500 requirements the model once
        # named company-content file names) can't be written to any row, so it is dropped and reported, not a stop
        # (ADR-0029). A contradiction left with fewer than two known members is dropped below.
        if req_id not in by_req:
            warnings.append(f"relations: {what} names unknown requirement {req_id}")
            return False
        return True

    def update(req_id: str, **fields):
        updates.setdefault(req_id, {}).update(fields)

    def flag(req_id: str, flag_type: str, detail: str):
        flags.append({"hrs_name": f"{flag_type} {req_id}"[:200], "hrs_flagtype": choice_value("hrs_flagtype", flag_type),
                      "hrs_severity": choice_value("hrs_severity", "Info"), "hrs_detail": detail,
                      "hrs_rfp@odata.bind": _bind("hrs_rfps", rfp_id),
                      "hrs_requirement@odata.bind": _bind("hrs_requirements", by_req[req_id]["hrs_requirementid"])})

    # Each duplicate points at a requirement that is not itself a duplicate. Batched relations (ADR-0029) can chain
    # (A of B, B of C) or loop (A of B, B of A); chains are followed to their root and a link closing a loop is dropped.
    dup_of: dict[str, str] = {}
    for d in relations.get("duplicates", []):
        if known(d["reqId"], "duplicate") and known(d["duplicateOf"], "duplicate target") and d["reqId"] != d["duplicateOf"]:
            if d["reqId"] not in dup_of:
                dup_of[d["reqId"]] = d["duplicateOf"]

    def dup_root(req_id: str) -> str | None:
        seen = {req_id}
        while req_id in dup_of:
            req_id = dup_of[req_id]
            if req_id in seen:
                return None
            seen.add(req_id)
        return req_id
    for d in relations.get("duplicates", []):
        target = dup_of.get(d["reqId"]) if dup_of.get(d["reqId"]) == d.get("duplicateOf") else None
        if target is None:
            continue
        root = dup_root(d["reqId"])
        if root is None:
            warnings.append(f"relations: {d['reqId']} duplicate of {target} closes a loop; not recorded")
            del dup_of[d["reqId"]]
            continue
        update(d["reqId"], **{"hrs_duplicateof@odata.bind": _bind("hrs_requirements", by_req[root]["hrs_requirementid"])})
        flag(d["reqId"], "DUPLICATE", f"Duplicate of {root}: {d['reason']}")
    for n, c in enumerate(relations.get("contradictions", []), start=1):
        ids = [i for i in c.get("reqIds", []) if known(i, "contradiction")]
        if len(ids) < 2:
            continue
        group = f"C{n:02d}"
        for i in ids:
            update(i, hrs_contradictiongroup=group)
            flag(i, "CONTRADICTION", f"Group {group} ({', '.join(ids)}): {c['reason']}")
    for a in relations.get("ambiguous", []):
        if known(a["reqId"], "ambiguous"):
            update(a["reqId"], hrs_ambiguous=True)
            flag(a["reqId"], "AMBIGUOUS", a["reason"])
    for o in relations.get("outOfScope", []):
        if known(o["reqId"], "out of scope"):
            update(o["reqId"], hrs_outofscope=True)
            flag(o["reqId"], "OUT-OF-SCOPE", o["reason"])
    # Two fixed shapes, so each flow loop writes the same columns every time.
    duplicate_updates, attribute_updates = [], []
    for k, v in sorted(updates.items()):
        row = by_req[k]
        if "hrs_duplicateof@odata.bind" in v:
            duplicate_updates.append({"id": row["hrs_requirementid"], "reqId": k, "duplicateOf": v["hrs_duplicateof@odata.bind"]})
        attribute_updates.append({"id": row["hrs_requirementid"], "reqId": k,
                                  "hrs_contradictiongroup": v.get("hrs_contradictiongroup", row.get("hrs_contradictiongroup")),
                                  "hrs_ambiguous": bool(v.get("hrs_ambiguous", row.get("hrs_ambiguous"))),
                                  "hrs_outofscope": bool(v.get("hrs_outofscope", row.get("hrs_outofscope")))})
    return {"duplicateUpdates": duplicate_updates, "attributeUpdates": attribute_updates,
            "updates": [{"id": by_req[k]["hrs_requirementid"], "reqId": k, "fields": v} for k, v in sorted(updates.items())],
            "flags": flags, "problems": problems, "warnings": warnings}


# --- Stage 5 ----------------------------------------------------------------------------------

def _answers(rows: Sequence[Mapping]) -> list[Answer]:
    code_by_row = {r.get("hrs_answerid"): r["hrs_answercode"] for r in rows}
    return [Answer(r["hrs_answercode"], r.get("hrs_question") or "", r.get("hrs_answertext") or "",
                   _expanded(r, "hrs_categorycode", "hrs_categorycode") or "",
                   choice_label("hrs_approvalstatus", r.get("hrs_approvalstatus")) or "",
                   _date(r.get("hrs_lastreviewed")),
                   choice_label("hrs_confidentiality", r.get("hrs_confidentiality")) or "Public",
                   code_by_row.get(r.get("_hrs_supersededby_value"), "")) for r in rows]


def _active(requirements: Sequence[Mapping]) -> list[Mapping]:
    active = choice_value("hrs_requirementstatus", "Active")
    return [r for r in requirements if r.get("hrs_status") == active and not r.get("_hrs_duplicateof_value")]


# Stage 5 prompts run in batches: one call over every requirement comes back cut short once an RFP has
# more than about 50 (RFP B has about 100). The flow loops over the batches and passes the outputs back as
# a list, which these functions merge.
SHORTLIST_BATCH = 25  # requirements per RFP Shortlist Answers call
SELECT_BATCH = 20  # requirements per RFP Select Answers call (each carries up to 8 candidate answers)


def _batches(items: Sequence, size: int) -> list[str]:
    return [json.dumps(list(items[i:i + size]), ensure_ascii=False) for i in range(0, len(items), size)]


def _merged(outputs: Mapping | Sequence[Mapping] | None) -> dict:
    """One prompt output ({items: [...]}) or a list of them (one per batch) -> one {items: [...]}."""
    if outputs is None:
        return {"items": []}
    batches = [outputs] if isinstance(outputs, Mapping) else outputs
    return {"items": [i for b in batches for i in (b or {}).get("items", [])]}


def shortlist_items(requirements: Sequence[Mapping], answers: Sequence[Mapping]) -> dict:
    """Prompt input for RFP Shortlist Answers: the questions (never the answer text) of every Approved answer,
    the questions of Retired and Draft answers in a separate flag-only list, and the active, non-duplicate
    requirements. ADR-0019."""
    library = sorted(_answers(answers), key=lambda a: a.answer_id)
    index = "\n".join(f"{a.answer_id} [{a.category}] {a.question}" for a in library if a.status == "Approved")
    unusable = "\n".join(f"{a.answer_id} [{a.status}] {a.question}" for a in library if a.status in ("Retired", "Draft"))
    # One prompt input carries both lists (the unusable one under its own heading), so the prompt's input
    # schema stays {Index, Items}.
    index = f"{index}\n\n### unusable list, flag only (one line: answerId [Retired or Draft] question):\n{unusable}"
    items = [{"reqId": r["hrs_reqid"], "text": r["hrs_requirementtext"]} for r in sorted(_active(requirements), key=lambda r: r["hrs_reqid"])]
    return {"index": index, "unusable": unusable, "items": items, "itemsJson": json.dumps(items, ensure_ascii=False), "count": len(items),
            "itemBatches": _batches(items, SHORTLIST_BATCH)}


def selection_items(requirements: Sequence[Mapping], answers: Sequence[Mapping], shortlist: Mapping | None = None,
                    top: int = 5, limit: int = 8) -> dict:
    """Prompt input for RFP Select Answers: active, non-duplicate requirements with Approved candidates only.

    Candidates are the meaning-based shortlist (any category, validated to Approved IDs) followed by the
    word-overlap shortlist in the requirement's category, up to `limit`. ADR-0019.
    """
    library = _answers(answers)
    approved = {a.answer_id: a for a in library if a.status == "Approved"}
    semantic = {i.get("reqId"): [x for x in i.get("answerIds", []) if x in approved] for i in _merged(shortlist)["items"]}
    items = []
    for r in sorted(_active(requirements), key=lambda r: r["hrs_reqid"]):
        category = _expanded(r, "hrs_categorycode", "hrs_categorycode") or ""
        ids = list(dict.fromkeys(semantic.get(r["hrs_reqid"], [])
                                 + [c.answer_id for c in select_answer_candidates(r["hrs_requirementtext"], category, library, top)]))[:limit]
        items.append({"reqId": r["hrs_reqid"], "text": r["hrs_requirementtext"],
                      "candidates": [{"answerId": i, "question": approved[i].question, "text": approved[i].text} for i in ids]})
    return {"items": items, "itemsJson": json.dumps(items, ensure_ascii=False), "count": len(items),
            "batches": _batches(items, SELECT_BATCH)}


def agree_selections(first: Mapping, second: Mapping | None) -> dict:
    """Self-consistency (ADR-0021): an answer stands only when both passes of RFP Select Answers find one.
    If one pass finds no candidate on the subject, the requirement is treated as unanswered (stage 6 decides
    whether that is a gap). If both find one but differ, the first pass's choice stands: the subject is
    answerable either way, and both choices were validated Approved candidates."""
    if not second:
        return dict(first)
    other = {s.get("reqId"): s.get("answerId") or "" for s in second.get("items", [])}
    items = []
    for s in first.get("items", []):
        if bool(s.get("answerId")) != bool(other.get(s.get("reqId"), "")):
            s = {**s, "answerId": "", "reason": "The two selection passes disagree on whether any candidate answers this requirement."}
        items.append(s)
    return {"items": items}


def resolve_selections(rfp_id: str, requirements: Sequence[Mapping], answers: Sequence[Mapping], items: Sequence[Mapping],
                       selections: Mapping | Sequence[Mapping], submission_date: str,
                       shortlist: Mapping | Sequence[Mapping] | None = None,
                       selections2: Mapping | Sequence[Mapping] | None = None) -> dict:
    """Validated selections -> hrs_response rows (answer required) and library flags. No selection -> a gap (stage 7).
    shortlist: the RFP Shortlist Answers output; its unusableIds (validated to Retired/Draft rows) only add flags."""
    selections = agree_selections(_merged(selections), _merged(selections2) if selections2 is not None else None)
    shortlist = _merged(shortlist)
    library = _answers(answers)
    status_of = {a.answer_id: a.status for a in library}
    unusable = {i.get("reqId"): [x for x in i.get("unusableIds", []) if status_of.get(x) in ("Retired", "Draft")]
                for i in (shortlist or {}).get("items", [])}
    same_topic = {i.get("reqId"): [x for x in i.get("answerIds", []) if status_of.get(x) == "Approved"]
                  for i in (shortlist or {}).get("items", [])}
    answer_rows = {r["hrs_answercode"]: r for r in answers}
    by_req = {r["hrs_reqid"]: r for r in requirements}
    candidates = {i["reqId"]: {c["answerId"] for c in i["candidates"]} for i in items}
    chosen = selections.get("items", [])
    problems = _check_ids(list(candidates), [s["reqId"] for s in chosen], "selection")
    problems += [f"selection: {s['reqId']} chose {s['answerId']}, not one of its candidates"
                 for s in chosen if s["answerId"] and s["answerId"] not in candidates.get(s["reqId"], set())]
    responses, flags, gaps = [], [], []
    submission = _date(submission_date)
    for s in chosen:
        req = by_req.get(s["reqId"])
        if req is None or (s["answerId"] and s["answerId"] not in candidates.get(s["reqId"], set())):
            continue
        req_bind = _bind("hrs_requirements", req["hrs_requirementid"])
        if not s["answerId"]:
            # Whether this is a gap is decided in stage 6 (compliance precedence); stage 7 flags NO-LIBRARY-ANSWER.
            gaps.append(s["reqId"])
            category = _expanded(req, "hrs_categorycode", "hrs_categorycode") or ""
            for flag_type, status, wording in (("LIBRARY-DRAFT-PRESENT", "Draft", "unapproved draft(s)"),
                                               ("LIBRARY-RETIRED-MATCH", "Retired", "retired answer(s)")):
                ids = related_unusable(req["hrs_requirementtext"], category, library, status, unusable.get(s["reqId"], []))
                if ids:
                    flags.append({"hrs_name": f"{flag_type} {s['reqId']}", "hrs_flagtype": choice_value("hrs_flagtype", flag_type),
                                  "hrs_severity": choice_value("hrs_severity", "Info"),
                                  "hrs_detail": f"No approved answer selected; {wording} {', '.join(ids)} match and must not be used.",
                                  "hrs_rfp@odata.bind": _bind("hrs_rfps", rfp_id), "hrs_requirement@odata.bind": req_bind})
            continue
        ann = annotate_answer(s["answerId"], req["hrs_requirementtext"], library, submission, unusable.get(s["reqId"], []),
                              same_topic.get(s["reqId"], []))
        responses.append({"hrs_name": f"{s['reqId']} response", "hrs_requirement@odata.bind": req_bind,
                          "hrs_answer@odata.bind": _bind("hrs_answers", answer_rows[s["answerId"]]["hrs_answerid"]),
                          "hrs_status": choice_value("hrs_responsestatus", "Not started"),
                          "hrs_smereviewrequired": ann.sme_review_required})
        for flag_type in ann.flags:
            related = {"LIBRARY-CONFLICT": ann.conflicting_answer_ids, "LIBRARY-RETIRED-MATCH": ann.retired_match_ids,
                       "LIBRARY-DRAFT-PRESENT": ann.draft_present_ids}.get(flag_type, [])
            detail = f"{s['answerId']}" + (f" vs {', '.join(related)}" if related else " last reviewed before the 18-month window")
            flags.append({"hrs_name": f"{flag_type} {s['reqId']}", "hrs_flagtype": choice_value("hrs_flagtype", flag_type),
                          "hrs_severity": choice_value("hrs_severity", "Info"), "hrs_detail": detail,
                          "hrs_rfp@odata.bind": _bind("hrs_rfps", rfp_id), "hrs_requirement@odata.bind": req_bind})
    # A duplicate cites the answer of the requirement it duplicates; it is not drafted separately (stage 6 skips it).
    chosen_by_req = {s["reqId"]: s["answerId"] for s in chosen if s["answerId"] in candidates.get(s["reqId"], set())}
    by_row = {r["hrs_requirementid"]: r for r in requirements}
    active = choice_value("hrs_requirementstatus", "Active")
    for req in sorted(requirements, key=lambda r: r["hrs_reqid"]):
        original = by_row.get(req.get("_hrs_duplicateof_value"))
        answer_id = chosen_by_req.get(original["hrs_reqid"]) if original else None
        if answer_id and req.get("hrs_status") == active:
            responses.append({"hrs_name": f"{req['hrs_reqid']} response",
                              "hrs_requirement@odata.bind": _bind("hrs_requirements", req["hrs_requirementid"]),
                              "hrs_answer@odata.bind": _bind("hrs_answers", answer_rows[answer_id]["hrs_answerid"]),
                              "hrs_status": choice_value("hrs_responsestatus", "Not started"), "hrs_smereviewrequired": False,
                              "hrs_responsetext": f"See the response to {original['hrs_reqid']}, which this requirement duplicates."})
    return {"responses": responses, "flags": flags, "gaps": gaps, "problems": problems}


# --- Stage 6 ----------------------------------------------------------------------------------

def _rule_thresholds(rules: Sequence[Mapping]) -> list[Threshold]:
    return [Threshold(r["hrs_ruleid"], r["hrs_thresholdmetric"], r["hrs_thresholdoperator"], float(r["hrs_thresholdvalue"]))
            for r in rules if r.get("hrs_thresholdmetric")]


def _certs(rows: Sequence[Mapping]) -> list[Certification]:
    return [Certification(c["hrs_certid"], c.get("hrs_name") or "", _date(c["hrs_issued"]), _date(c["hrs_expires"])) for c in rows]


def rule_screen_items(requirements: Sequence[Mapping], rules: Sequence[Mapping]) -> dict:
    """Prompt input for RFP Screen Rules: the rule register (ID, severity, condition) and the active,
    non-duplicate requirements. ADR-0019."""
    index = "\n".join(f"{r['hrs_ruleid']} [{choice_label('hrs_severity', r.get('hrs_severity')) or ''}] {r.get('hrs_condition') or ''}"
                      for r in sorted(rules, key=lambda r: r["hrs_ruleid"]))
    items = [{"reqId": r["hrs_reqid"], "text": r["hrs_requirementtext"]} for r in sorted(_active(requirements), key=lambda r: r["hrs_reqid"])]
    return {"index": index, "items": items, "itemsJson": json.dumps(items, ensure_ascii=False), "count": len(items),
            "itemBatches": _batches(items, SCREEN_BATCH)}


def adjudication_items(requirements: Sequence[Mapping], rules: Sequence[Mapping], certifications: Sequence[Mapping],
                       submission_date: str, screen: Mapping | Sequence[Mapping] | None = None) -> dict:
    """Candidate rule hits with threshold evidence, and the certificate status text, for RFP Adjudicate Rules.

    Candidates are the trigger-phrase and threshold matches plus the meaning-based screen (validated to known
    rule IDs). The screen only adds candidates; adjudication still decides whether a rule fires. ADR-0019.
    """
    active = sorted(_active(requirements), key=lambda r: r["hrs_reqid"])
    triggers = {r["hrs_ruleid"]: r.get("hrs_triggerpattern") or "" for r in rules}
    rule_by_id = {r["hrs_ruleid"]: r for r in rules}
    thresholds = _rule_thresholds(rules)
    screened = {i.get("reqId"): [x for x in i.get("ruleIds", []) if x in rule_by_id] for i in _merged(screen)["items"]}
    items = []
    for req in active:
        evidence = {t.rule_id: t for t in evaluate_thresholds(req["hrs_requirementtext"], thresholds)}
        matched = [c.rule_id for c in match_rule_candidates(req["hrs_requirementtext"], triggers, thresholds)]
        for rule_id in dict.fromkeys(matched + screened.get(req["hrs_reqid"], [])):
            rule = rule_by_id[rule_id]
            ev = evidence.get(rule_id)
            items.append({"key": f"{req['hrs_reqid']}|{rule_id}", "reqId": req["hrs_reqid"], "text": req["hrs_requirementtext"],
                          "ruleId": rule_id, "trigger": rule.get("hrs_triggerpattern") or "", "condition": rule.get("hrs_condition") or "",
                          "thresholdEvidence": ev.to_dict() if ev else None})
    certs = _certs(certifications)
    mentions = certifications_mentioned([{"reqId": r["hrs_reqid"], "text": r["hrs_requirementtext"]} for r in active], certs, _date(submission_date))
    status_text = "\n".join(f"{c.cert_id} {c.name} - {'valid' if s else 'NOT valid'} on {submission_date} (expires {c.expires.isoformat()})"
                            for c, s in ((c, c.issued <= _date(submission_date) <= c.expires) for c in certs))
    prompt_items = [{k: v for k, v in i.items() if k != "key"} for i in items]
    return {"items": items, "itemsJson": json.dumps(prompt_items, ensure_ascii=False), "count": len(items),
            "batches": _batches(prompt_items, ADJUDICATE_BATCH), "certificationsText": status_text, "mentions": mentions}


DRAFT_BATCH = 12  # requirements per RFP Draft Responses call


def resolve_policy(rfp_id: str, requirements: Sequence[Mapping], rules: Sequence[Mapping], certifications: Sequence[Mapping],
                   responses: Sequence[Mapping], items: Sequence[Mapping], adjudications: Mapping | Sequence[Mapping], mentions: Sequence[Mapping],
                   buyer_codes: Sequence[Mapping]) -> dict:
    """Fired rules -> POLICY-RULE / CERTIFICATION-INVALID flags, compliance per requirement, and drafting items.
    adjudications: one RFP Adjudicate Rules output, or a list of them (one per batch)."""
    adjudications = _merged(adjudications)
    by_req = {r["hrs_reqid"]: r for r in requirements}
    rule_by_id = {r["hrs_ruleid"]: r for r in rules}
    cert_rows = {c["hrs_certid"]: c for c in certifications}
    expected = [f"{i['reqId']}|{i['ruleId']}" for i in items]
    got = [f"{a['reqId']}|{a['ruleId']}" for a in adjudications.get("items", [])]
    problems = _check_ids(expected, got, "adjudication")
    fired: dict[str, list[Mapping]] = {}
    for a in adjudications.get("items", []):
        if a.get("fired") and f"{a['reqId']}|{a['ruleId']}" in set(expected):
            fired.setdefault(a["reqId"], []).append({**a, "rule": rule_by_id[a["ruleId"]]})
    flags, cert_flags = [], []
    for req_id, hits in sorted(fired.items()):
        req_bind = _bind("hrs_requirements", by_req[req_id]["hrs_requirementid"])
        for h in hits:
            flags.append({"hrs_name": f"POLICY-RULE {h['ruleId']} {req_id}", "hrs_flagtype": choice_value("hrs_flagtype", "POLICY-RULE"),
                          "hrs_severity": h["rule"]["hrs_severity"], "hrs_detail": h.get("reason", ""),
                          "hrs_rfp@odata.bind": _bind("hrs_rfps", rfp_id), "hrs_requirement@odata.bind": req_bind,
                          "hrs_policyrule@odata.bind": _bind("hrs_policyrules", h["rule"]["hrs_policyruleid"])})
    for m in mentions:
        for c in m["certifications"]:
            if not c["valid"] and m["reqId"] in by_req:
                cert_flags.append({"hrs_name": f"CERTIFICATION-INVALID {c['certId']} {m['reqId']}",
                              "hrs_flagtype": choice_value("hrs_flagtype", "CERTIFICATION-INVALID"),
                              "hrs_severity": choice_value("hrs_severity", "Block"),
                              "hrs_detail": f"{c['name']} expired {c['expires']}; not valid on the submission date.",
                              "hrs_rfp@odata.bind": _bind("hrs_rfps", rfp_id),
                              "hrs_requirement@odata.bind": _bind("hrs_requirements", by_req[m["reqId"]]["hrs_requirementid"]),
                              "hrs_certification@odata.bind": _bind("hrs_certifications", cert_rows[c["certId"]]["hrs_certificationid"])})
    answered = {r["_hrs_requirement_value"]: r for r in responses}
    codes = [(c["code"], c["meaning"]) for c in buyer_codes]
    updates, draft_items, by_row = [], [], {}
    dup_of = {r["hrs_requirementid"]: r.get("_hrs_duplicateof_value") for r in requirements}

    def root_of(row_id: str) -> str | None:
        """The requirement at the end of a duplicate chain, or None for a loop or a target outside the RFP."""
        seen = {row_id}
        while dup_of.get(row_id):
            row_id = dup_of[row_id]
            if row_id in seen or row_id not in dup_of:
                return None
            seen.add(row_id)
        return row_id
    for req in sorted(requirements, key=lambda r: r["hrs_reqid"]):
        status = choice_label("hrs_requirementstatus", req.get("hrs_status")) or "Active"
        if req.get("_hrs_duplicateof_value") and root_of(req["hrs_requirementid"]):
            continue  # second pass below: a duplicate takes the compliance of the requirement its chain ends at
        severities = [choice_label("hrs_severity", h["rule"]["hrs_severity"]) for h in fired.get(req["hrs_reqid"], [])]
        response = answered.get(req["hrs_requirementid"])
        compliance = derive_compliance(status=status, has_approved_answer=response is not None,
                                       out_of_scope=bool(req.get("hrs_outofscope")), fired_severities=severities,
                                       ambiguous=bool(req.get("hrs_ambiguous")),
                                       in_contradiction_group=bool(req.get("hrs_contradictiongroup"))).value
        by_row[req["hrs_requirementid"]] = compliance
        updates.append({"id": req["hrs_requirementid"], "reqId": req["hrs_reqid"],
                        "fields": {"hrs_compliance": choice_value("hrs_compliance", compliance)}})
        if response is not None:
            positions = [h["rule"].get("hrs_approvedposition") or "" for h in fired.get(req["hrs_reqid"], [])]
            draft_items.append({"reqId": req["hrs_reqid"], "responseId": response["hrs_responseid"], "text": req["hrs_requirementtext"],
                                "mandatory": choice_label("hrs_mandatory", req.get("hrs_mandatory")) or "",
                                "compliance": compliance,
                                # An RFP with no response codes of its own (RFP B) takes our compliance value as the code.
                                "allowedCodes": map_response_code(compliance, codes) or [c for c, _ in codes] or [compliance],
                                "flags": [f"POLICY-RULE {h['ruleId']}" for h in fired.get(req["hrs_reqid"], [])]
                                + (["SME-REVIEW"] if response.get("hrs_smereviewrequired") else []),
                                "approvedPosition": " ".join(p for p in positions if p),
                                "answer": {"answerId": _expanded(response, "hrs_answer", "hrs_answercode") or "",
                                           "question": _expanded(response, "hrs_answer", "hrs_question") or "",
                                           "text": _expanded(response, "hrs_answer", "hrs_answertext") or ""}})
    for req in sorted(requirements, key=lambda r: r["hrs_reqid"]):
        original = by_row.get(root_of(req["hrs_requirementid"])) if req.get("_hrs_duplicateof_value") else None
        if original:
            updates.append({"id": req["hrs_requirementid"], "reqId": req["hrs_reqid"],
                            "fields": {"hrs_compliance": choice_value("hrs_compliance", original)}})
    prompt_items = [{k: v for k, v in i.items() if k != "responseId"} for i in draft_items]
    # Drafting runs in batches: one call for every response can come back cut short.
    batches = [json.dumps(prompt_items[i:i + DRAFT_BATCH], ensure_ascii=False) for i in range(0, len(prompt_items), DRAFT_BATCH)]
    return {"flags": flags + cert_flags, "policyFlags": flags, "certificationFlags": cert_flags, "complianceUpdates": updates, "draftItems": draft_items,
            "draftItemsJson": json.dumps(prompt_items, ensure_ascii=False), "draftBatches": batches, "draftCount": len(draft_items), "problems": problems}


def resolve_drafts(draft_items: Sequence[Mapping], drafts: Mapping | Sequence[Mapping], word_limit: int | None, counting_rule: str) -> dict:
    """drafts: one RFP Draft Responses output, or a list of them (one per batch)."""
    by_req = {d["reqId"]: d for d in draft_items}
    got = [g for batch in ([drafts] if isinstance(drafts, Mapping) else drafts) for g in (batch or {}).get("items", [])]
    problems = _check_ids(list(by_req), [g["reqId"] for g in got], "drafting")
    updates, breaches = [], []
    for g in got:
        item = by_req.get(g["reqId"])
        if item is None:
            continue
        if g["responseCode"] not in item["allowedCodes"]:
            problems.append(f"drafting: {g['reqId']} used code {g['responseCode']!r}, allowed {item['allowedCodes']}")
            continue
        words = count_words(g["responseText"], counting_rule)
        if word_limit and words > word_limit:
            breaches.append({"reqId": g["reqId"], "words": words, "limit": word_limit})  # gate G6 decides, not this stage
        updates.append({"id": item["responseId"], "reqId": g["reqId"], "fields": {
            "hrs_responsetext": g["responseText"], "hrs_responsecode": g["responseCode"][:40], "hrs_wordcount": words,
            "hrs_status": choice_value("hrs_responsestatus", "SME review" if "SME-REVIEW" in item["flags"] else "Drafted")}})
    return {"updates": updates, "limitBreaches": breaches, "problems": problems}


# --- Stage 7 ----------------------------------------------------------------------------------

def gap_rows(requirements: Sequence[Mapping], responses: Sequence[Mapping], flags: Sequence[Mapping], rules: Sequence[Mapping],
             categories: Sequence[Mapping], due_by: str, rfp_id: str = "", proposals: Sequence[Mapping] = (), rfp_key: str = "") -> dict:
    """Active, non-duplicate requirements whose compliance is Gap -> hrs_gap rows routed by the documented rule (9.1),
    and a NO-LIBRARY-ANSWER flag for each.

    proposals: this RFP's Draft PROP-<key>-<reqId> entries. One whose question is exactly the requirement's current text
    was answered by an SME on an earlier pass, so its gap is written as Answered and no card goes out (reusedRows, ADR-0033).
    Any change to the text asks again. `count` is what the run must wait for, so reused rows don't count."""
    earlier = {p["hrs_answercode"]: p for p in proposals}
    answered = {r["_hrs_requirement_value"] for r in responses}
    gap = choice_value("hrs_compliance", "Gap")
    primary = {c["hrs_categorycode"]: _expanded(c, "hrs_primarysmeid", "hrs_smecode") for c in categories}
    rule_by_row = {r["hrs_policyruleid"]: r for r in rules}
    policy = choice_value("hrs_flagtype", "POLICY-RULE")
    rows = []
    reused = []
    gap_flags = []
    for req in sorted(_active(requirements), key=lambda r: r["hrs_reqid"]):
        if req["hrs_requirementid"] in answered or req.get("hrs_compliance") != gap:
            continue
        fired = []
        for f in flags:
            if f.get("_hrs_requirement_value") == req["hrs_requirementid"] and f.get("hrs_flagtype") == policy and f.get("_hrs_policyrule_value"):
                rule = rule_by_row[f["_hrs_policyrule_value"]]
                fired.append(FiredRule(rule["hrs_ruleid"], Severity(choice_label("hrs_severity", rule["hrs_severity"])),
                                       _expanded(rule, "hrs_escalationsmeid", "hrs_smecode") or ""))
        category = _expanded(req, "hrs_categorycode", "hrs_categorycode") or ""
        sme = route_sme(category, fired, primary)
        row = {"hrs_name": f"Gap {req['hrs_reqid']}", "reqId": req["hrs_reqid"],
               "hrs_requirement@odata.bind": _bind("hrs_requirements", req["hrs_requirementid"]),
               "hrs_routedtosme@odata.bind": _key_bind("hrs_smes", "hrs_smecode", sme), "smeCode": sme,
               "hrs_dueby": due_by, "hrs_smestatus": choice_value("hrs_smestatus", "Routed")}
        prior = earlier.get(f"PROP-{rfp_key}-{req['hrs_reqid']}")
        reuse = bool(prior and prior.get("hrs_question") == req["hrs_requirementtext"] and (prior.get("hrs_answertext") or "").strip())
        if reuse:
            reused.append({**row, "hrs_smestatus": choice_value("hrs_smestatus", "Answered"), "hrs_smeanswertext": prior["hrs_answertext"],
                           "hrs_proposedanswer@odata.bind": _bind("hrs_answers", prior["hrs_answerid"])})
        else:
            rows.append(row)
        note = "the SME's earlier answer was reused because the requirement text is unchanged" if reuse else f"routed to {sme}"
        gap_flags.append({"hrs_name": f"NO-LIBRARY-ANSWER {req['hrs_reqid']}", "hrs_flagtype": choice_value("hrs_flagtype", "NO-LIBRARY-ANSWER"),
                          "hrs_severity": choice_value("hrs_severity", "Info"),
                          "hrs_detail": f"No approved library answer addresses this requirement; {note}.",
                          "hrs_rfp@odata.bind": _bind("hrs_rfps", rfp_id), "hrs_requirement@odata.bind": _bind("hrs_requirements", req["hrs_requirementid"])})
    return {"rows": rows, "count": len(rows), "reusedRows": reused, "flags": gap_flags}


def clarification_items(requirements: Sequence[Mapping], flags: Sequence[Mapping]) -> dict:
    """One item per ambiguous requirement and per contradiction group, for RFP Draft Clarifications."""
    by_id = {r["hrs_requirementid"]: r for r in requirements}
    reasons = {}
    for f in flags:
        label = choice_label("hrs_flagtype", f.get("hrs_flagtype"))
        if label in ("AMBIGUOUS", "CONTRADICTION") and f.get("_hrs_requirement_value") in by_id:
            reasons.setdefault((label, by_id[f["_hrs_requirement_value"]]["hrs_reqid"]), f.get("hrs_detail") or "")
    items, groups = [], {}
    for req in sorted(requirements, key=lambda r: r["hrs_reqid"]):
        if req.get("hrs_contradictiongroup"):
            groups.setdefault(req["hrs_contradictiongroup"], []).append(req)
        elif req.get("hrs_ambiguous"):
            items.append({"key": req["hrs_reqid"], "reqIds": [req["hrs_reqid"]], "trigger": "Ambiguity",
                          "reason": reasons.get(("AMBIGUOUS", req["hrs_reqid"]), ""), "section": req.get("hrs_section") or "",
                          "requirements": [{"reqId": req["hrs_reqid"], "text": req["hrs_requirementtext"]}]})
    for group, reqs in sorted(groups.items()):
        items.append({"key": group, "reqIds": [r["hrs_reqid"] for r in reqs], "trigger": "Contradiction",
                      "reason": reasons.get(("CONTRADICTION", reqs[0]["hrs_reqid"]), ""), "section": reqs[0].get("hrs_section") or "",
                      "requirements": [{"reqId": r["hrs_reqid"], "text": r["hrs_requirementtext"]} for r in reqs]})
    prompt_items = [{k: v for k, v in i.items() if k != "section"} for i in items]
    return {"items": items, "itemsJson": json.dumps(prompt_items, ensure_ascii=False), "count": len(items),
            "batches": _batches(prompt_items, CLARIFY_BATCH)}


def resolve_clarifications(rfp_id: str, items: Sequence[Mapping], drafted: Mapping | Sequence[Mapping]) -> dict:
    """drafted: one RFP Draft Clarifications output, or a list of them (one per batch)."""
    by_key = {i["key"]: i for i in items}
    got = _merged(drafted)["items"]
    problems = _check_ids(list(by_key), [g["key"] for g in got], "clarification")
    rows, letter_rows = [], []
    for n, g in enumerate(sorted((g for g in got if g["key"] in by_key), key=lambda g: g["key"]), start=1):
        item = by_key[g["key"]]
        rows.append({"hrs_name": f"Clarification {n}: {', '.join(item['reqIds'])}"[:200], "hrs_rfp@odata.bind": _bind("hrs_rfps", rfp_id),
                     "hrs_requirementids": ", ".join(item["reqIds"]), "hrs_trigger": choice_value("hrs_clarificationtrigger", item["trigger"]),
                     "hrs_questiontext": g["questionText"], "hrs_status": choice_value("hrs_clarificationstatus", "Drafted")})
        letter_rows.append({"n": str(n), "ReqIDs": ", ".join(item["reqIds"]), "Section": item["section"], "QuestionText": g["questionText"]})
    return {"rows": rows, "letterRows": letter_rows, "problems": problems}


# --- Stage 8 ----------------------------------------------------------------------------------

SECTIONS = [
    # (token, title, guidance, word budget, categories whose responses inform it)
    ("CoverLetterBody", "Cover Letter", "Brief cover letter: thank the buyer, state the respondent's commitment and point to the executive summary.", 220, None),
    ("ExecutiveSummary", "Executive Summary", "One page; restate the buyer's top three objectives and map each to the proposed solution.", 380, None),
    ("CompanyQualifications", "Company Qualifications and Experience", "Company profile, history and relevant experience.", 420, ["COMP"]),
    ("CaseStudies", "Case Studies", "Two or three relevant case studies from the company content, with measurable results.", 420, ["COMP"]),
    ("SolutionNarrative", "Proposed Solution", "How the solution meets the functional requirements.", 700, ["WORK", "ASSET", "ANLY", "INTG"]),
    ("ImplementationApproach", "Implementation Approach", "Implementation methodology, phases and responsibilities.", 520, ["IMPL"]),
    ("ScheduleTable", "Schedule", "A short phase-by-phase schedule as plain sentences (phase, duration, key outputs).", 180, ["IMPL"]),
    ("TrainingApproach", "Training and Change Management", "Training approach and change management.", 380, ["IMPL", "SUPP"]),
    ("SupportModel", "Support and Service Levels", "Support model, hours, response targets and service levels.", 420, ["SUPP", "AVAIL"]),
    ("SecurityNarrative", "Security and Hosting", "Security controls, hosting, data protection and privacy.", 520, ["SEC", "PRIV", "TECH"]),
]


def section_inputs(requirements: Sequence[Mapping], responses: Sequence[Mapping], opportunity: Mapping,
                   budget_scale: float = 1.0) -> list[dict]:
    by_req_id = {r["hrs_requirementid"]: r for r in requirements}
    drafted = []
    for resp in responses:
        req = by_req_id.get(resp.get("_hrs_requirement_value"))
        if req and resp.get("hrs_responsetext"):
            drafted.append((_expanded(req, "hrs_categorycode", "hrs_categorycode") or "", req["hrs_reqid"], resp["hrs_responsetext"]))
    drafted.sort(key=lambda d: d[1])
    context = f"{opportunity.get('hrs_buyer', '')} ({opportunity.get('hrs_industry', '')}): {opportunity.get('hrs_title', '')}"
    out = []
    for token, title, guidance, budget, cats in SECTIONS:
        chosen = [d for d in drafted if cats is None or d[0] in cats][:40]
        out.append({"token": token, "sectionName": title, "guidance": guidance, "wordBudget": str(int(budget * budget_scale)),
                    "buyerContext": context, "responses": "\n".join(f"{r}: {t}" for _, r, t in chosen)[:20000]})
    return out


def package_requirements(requirements: Sequence[Mapping], responses: Sequence[Mapping], flags: Sequence[Mapping],
                         rules: Sequence[Mapping], gaps: Sequence[Mapping]) -> list[dict]:
    resp_by_req = {r["_hrs_requirement_value"]: r for r in responses}
    gap_by_req = {g["_hrs_requirement_value"]: g for g in gaps}
    rule_by_row = {r["hrs_policyruleid"]: r for r in rules}
    by_id = {r["hrs_requirementid"]: r for r in requirements}
    policy = choice_value("hrs_flagtype", "POLICY-RULE")
    rows = []
    for req in sorted(requirements, key=lambda r: r["hrs_reqid"]):
        resp = resp_by_req.get(req["hrs_requirementid"], {})
        gap = gap_by_req.get(req["hrs_requirementid"], {})
        req_flags = [f for f in flags if f.get("_hrs_requirement_value") == req["hrs_requirementid"]]
        policy_flags = []
        for f in req_flags:
            if f.get("hrs_flagtype") == policy and f.get("_hrs_policyrule_value") in rule_by_row:
                rule = rule_by_row[f["_hrs_policyrule_value"]]
                policy_flags.append({"ruleId": rule["hrs_ruleid"], "severity": choice_label("hrs_severity", rule["hrs_severity"]),
                                     "approvedPosition": rule.get("hrs_approvedposition") or ""})
        compliance = choice_label("hrs_compliance", req.get("hrs_compliance"))
        duplicate_of = by_id.get(req.get("_hrs_duplicateof_value"))
        if duplicate_of and not resp:
            resp = {"hrs_responsetext": f"See the response to {duplicate_of['hrs_reqid']}, which this requirement duplicates.",
                    **{k: v for k, v in resp_by_req.get(duplicate_of["hrs_requirementid"], {}).items() if k != "hrs_responsetext"}}
        rows.append({
            "reqId": req["hrs_reqid"], "location": req.get("hrs_location") or "", "text": req["hrs_requirementtext"],
            "category": _expanded(req, "hrs_categorycode", "hrs_categorycode") or "",
            "mandatory": choice_label("hrs_mandatory", req.get("hrs_mandatory")) or "",
            "compliance": compliance or "", "responseCode": resp.get("hrs_responsecode") or "",
            "responseText": resp.get("hrs_responsetext") or "", "answerId": _expanded(resp, "hrs_answer", "hrs_answercode") or "",
            "smeAnswer": gap.get("hrs_smeanswertext") or "", "policyFlags": policy_flags,
            "otherFlags": sorted({choice_label("hrs_flagtype", f["hrs_flagtype"]) for f in req_flags if f.get("hrs_flagtype") != policy}),
            "owner": "", "status": choice_label("hrs_responsestatus", resp.get("hrs_status")) or ("SME review" if gap else ""),
        })
    return rows


def approval_summary(requirements: Sequence[Mapping], gaps: Sequence[Mapping], clarifications: Sequence[Mapping],
                     package_rows: Sequence[Mapping]) -> dict:
    counts = {k: sum(1 for r in package_rows if r["compliance"] == k) for k in ("Full", "Partial", "No", "Gap", "Clarify")}
    exceptions = [{"ruleId": f["ruleId"], "severity": f["severity"], "reqId": r["reqId"], "summary": r["text"][:120]}
                  for r in package_rows for f in r["policyFlags"] if f["severity"] in ("Block", "Warn")]
    return {"totalRequirements": len(package_rows), "countFull": counts["Full"], "countPartial": counts["Partial"],
            "countNo": counts["No"], "countGap": counts["Gap"], "countClarify": counts["Clarify"],
            "gapsRouted": len(gaps), "clarificationsSent": len(clarifications), "exceptions": exceptions,
            "flagSeverities": sorted({e["severity"] for e in exceptions})}


def gate_inputs(package_rows: Sequence[Mapping], responses: Sequence[Mapping], word_limit: int | None) -> dict:
    """Inputs for gates G5 (every Block/Warn flag has an exception entry) and G6 (word counts)."""
    g5 = [{"flag_type": "POLICY-RULE", "severity": f["severity"], "rule_id": f["ruleId"], "req_id": r["reqId"]}
          for r in package_rows for f in r["policyFlags"]]
    words = {}
    for resp in responses:
        if resp.get("hrs_wordcount") is not None:
            words[resp.get("hrs_name", resp.get("hrs_responseid", ""))] = int(resp["hrs_wordcount"])
    return {"g5Flags": g5, "wordCounts": words, "wordLimit": word_limit or None}