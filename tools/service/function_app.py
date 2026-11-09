"""rfp-tools HTTP service (Azure Functions, Python v2 model). Fronted by the rfp-tools custom connector.

Every route is POST, JSON in / JSON out, function-key auth. Binary documents travel base64-encoded.
Endpoints are batch-shaped (one call per stage, not per requirement): each agent-flow action costs
Copilot credits (ARCHITECTURE.md 3.1), and the answer library should cross the wire once per run.
All logic lives in rfp_tools; this file only translates JSON.
"""

import base64
import json
import logging
import os
from datetime import date

import azure.functions as func

from rfp_tools import (
    Certification,
    FiredRule,
    check_certifications,
    count_words,
    derive_approval_level,
    derive_compliance,
    route_sme,
)
from rfp_tools.assembly import fill_template, heading1_titles, split_sections
from rfp_tools.content import certifications_mentioned, company_content, facts_text, match_opportunity, render_card
from rfp_tools.package import assemble_package
from rfp_tools import orchestrator, stages
from rfp_tools.documents import count_body_elements, normalize_docx
from rfp_tools.gates import count_pages, gate_g1, gate_g2, gate_g4, gate_g5, gate_g6
from rfp_tools.library import Answer, annotate_answer, map_response_code, select_answer_candidates
from rfp_tools.matrix import fill_compliance_matrix
from rfp_tools import addendum, ocr
from rfp_tools.pdf_layout import TransientError, analyze_layout, normalize_layout
from rfp_tools.requirements import (
    assign_requirement_ids,
    extract_table_requirements,
    id_prefix,
    labeled_requirements,
    prose_requirements,
    resolve_identified,
    sentence_candidates,
)
from rfp_tools.rules import match_rule_candidates
from rfp_tools.thresholds import Threshold, evaluate_thresholds

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
ROUTES = {}


def route(name, operation_id, summary):
    """Registers a handler; (operation_id, summary) also feed the custom connector's OpenAPI definition."""
    def register(handler):
        ROUTES[name] = handler
        handler.operation_id, handler.summary = operation_id, summary
        return handler
    return register


def _b64(s: str) -> bytes:
    return base64.b64decode(s)


def _to_b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s[:10]) if s else None


def _library(items) -> list[Answer]:
    return [Answer(a["answerId"], a.get("question", ""), a.get("text", ""), a["category"], a["status"],
                   _date(a.get("lastReviewed")), a.get("confidentiality") or "Public") for a in items]


def _thresholds(rules) -> list[Threshold]:
    return [Threshold(r["ruleId"], r["metric"], r["operator"], float(r["value"])) for r in rules if r.get("metric")]


def _blocks(file_name: str, content: bytes):
    """Blocks of a .docx (Open XML) or native .pdf (Document Intelligence Layout), with the gate G1 counts."""
    name = file_name.lower()
    if name.endswith(".docx"):
        blocks = normalize_docx(content)
        return blocks, len(blocks), count_body_elements(content), "body element"
    if name.endswith(".pdf"):
        # The endpoint is an app setting, never a request field: the call carries this app's identity token.
        blocks = normalize_layout(analyze_layout(content, os.environ["DOCINTEL_ENDPOINT"]))
        covered = {p for b in blocks for p in b.pages}
        return blocks, len(covered), count_pages(content), "page"
    raise ValueError("Only .docx and .pdf (native or scanned) are supported; Excel questionnaires are out of scope (ADR-0024)")


def _low_blocks(blocks) -> dict:
    """Blocks read below the OCR threshold, by index, with what a verification card needs (ADR-0025)."""
    return {str(b.index): {"confidence": b.confidence, "page": b.page, "regions": b.regions}
            for b in blocks if b.confidence < ocr.OCR_CONFIDENCE_MIN}


def _low_confidence(requirements: list[dict], low_blocks: dict) -> list[dict]:
    blocks = [{"index": int(i), **v} for i, v in low_blocks.items()]
    return ocr.low_confidence(requirements, blocks)


@route("normalize-document", "NormalizeDocument", "Normalize a document into ordered blocks and the instructions text (stage 1)")
def normalize_document(body):
    content = _b64(body["contentBase64"])
    name = body.get("fileName", "")
    blocks, covered, units, unit = _blocks(name, content)
    g1 = gate_g1(covered, units, unit)
    requirement_blocks = [r.source_block_index for r in extract_table_requirements(blocks)]
    pdf = name.lower().endswith(".pdf")
    scanned = pdf and ocr.is_scanned(content)
    # Document row facts (ADR-0002): format, whether OCR was used, mean block confidence, page count.
    return {"blocks": [b.to_dict() for b in blocks], "gateG1": g1.to_dict(), "blockCount": len(blocks),
            "factsText": facts_text(blocks, requirement_blocks),
            "format": "PdfScanned" if scanned else ("PdfNative" if pdf else "Docx"), "ocrUsed": scanned,
            "meanConfidence": round(sum(b.confidence for b in blocks) / len(blocks), 4) if blocks else 1.0,
            "pageCount": units if pdf else None}


@route("extract-requirements", "ExtractRequirements", "Extract and identify table requirements, with gate G2 (stage 2)")
def extract_requirements(body):
    content = _b64(body["contentBase64"])
    blocks = normalize_docx(content)
    prefix = id_prefix(body.get("rfpNumber", ""), body.get("idPrefix", "REQ"))
    candidates = assign_requirement_ids(extract_table_requirements(blocks), prefix)
    reqs = [c.to_dict() for c in candidates]
    g2 = gate_g2(reqs, int(body.get("expectedMin", 1)), int(body.get("expectedMax", 1000)))
    return {"requirements": reqs, "gateG2": g2.to_dict()}


@route("requirement-candidates", "RequirementCandidates", "Candidate sentences of a prose document for the identify prompt (stage 2)")
def requirement_candidates(body):
    blocks, _, _, _ = _blocks(body.get("fileName", ""), _b64(body["contentBase64"]))
    low = _low_blocks(blocks)
    # A document that leads its requirements with buyer IDs is extracted by code; no prompt (ADR-0022 amendment).
    labeled = [r.to_dict() for r in labeled_requirements(blocks)]
    if labeled:
        g2 = gate_g2(labeled, int(body.get("expectedMin", 1)), int(body.get("expectedMax", 1000)))
        return {"mode": "labeled", "requirements": labeled, "gateG2": g2.to_dict(), "lowConfidence": _low_confidence(labeled, low),
                "candidates": [], "candidatesJson": "[]", "candidateBatches": [], "count": 0, "lowBlocks": low}
    candidates = sentence_candidates(blocks)
    shape = [{k: c[k] for k in ("id", "section", "where", "text")} for c in candidates]
    batches = [json.dumps(shape[i:i + stages.IDENTIFY_BATCH], ensure_ascii=False) for i in range(0, len(shape), stages.IDENTIFY_BATCH)]
    return {"mode": "prose", "requirements": [], "gateG2": {"gate": "G2", "passed": True, "problems": []}, "lowConfidence": [],
            "candidates": candidates, "candidatesJson": json.dumps(shape, ensure_ascii=False), "candidateBatches": batches,
            "count": len(candidates), "lowBlocks": low}


@route("resolve-requirements", "ResolveRequirements", "Stage 2: requirements from the identify passes, with IDs and gate G2")
def resolve_requirements(body):
    resolved = resolve_identified(body["candidates"], body.get("passes") or [])
    prefix = id_prefix(body.get("rfpNumber", ""), "REQ")
    reqs = [c.to_dict() for c in assign_requirement_ids(prose_requirements(resolved["candidates"]), prefix)]
    g2 = gate_g2(reqs, int(body.get("expectedMin", 1)), int(body.get("expectedMax", 1000)))
    # Unknown IDs from one pass never reach the majority, so they are reported rather than stopping the run.
    return {"requirements": reqs, "gateG2": g2.to_dict(), "warnings": resolved["problems"],
            "lowConfidence": _low_confidence(reqs, body.get("lowBlocks") or {})}


@route("ocr-crop", "OcrCrop", "Card-sized page-image crop around a block, for OCR verification")
def ocr_crop(body):
    regions = body["regions"]
    if isinstance(regions, str):
        regions = json.loads(regions)
    return {"jpegBase64": _to_b64(ocr.crop(_b64(body["contentBase64"]), regions, for_card=True))}


@route("addendum-items", "AddendumItems", "Stage 3: addendum text, item labels and locations for the interpret prompt")
def addendum_items(body):
    blocks = body["blocks"]
    revision = int(body["revision"])
    prompt = addendum.prompt_input(blocks)
    label_blocks = {b["label"]: b["index"] for b in blocks if b.get("label")}
    return {"text": prompt["text"], "labels": prompt["labels"], "locations": addendum.item_locations(blocks, revision),
            "labelBlocks": label_blocks}


@route("apply-addendum", "ApplyAddendum", "Stage 3: verified addendum changes as requirement, flag and RFP writes")
def apply_addendum(body):
    revision = int(body["revision"])
    requirements = body["requirements"]
    agreed = addendum.agree(body.get("passes") or [])
    validated = addendum.validate(agreed, body["addendumText"], [r["hrs_reqid"] for r in requirements], body.get("labels", []))
    forms = body.get("requiredForms") or []
    if isinstance(forms, str):
        forms = json.loads(forms or "[]")
    out = addendum.apply(body["rfpId"], revision, requirements, validated, body.get("locations", {}), forms)
    label_blocks = body.get("labelBlocks", {})
    # Added requirements come back in the extraction shape, so the stage classifies and writes them like stage 2.
    for item, a in zip([c.get("item", "") for c in validated["changes"] if c["kind"] == "add"], out["added"]):
        a["source_block_index"] = int(label_blocks.get(item, 0))
    # The flow writes with fixed columns, so updates are split by kind and flags by whether they name a requirement.
    def kind(column):
        return [{"id": u["id"], "reqId": u["reqId"], "value": u["fields"][column]} for u in out["updates"] if column in u["fields"]]
    rfp = {"hrs_duedate": body.get("currentDueDate"), "hrs_pagelimit": body.get("currentPageLimit"), **out["rfp"]}
    return {**out, "rfp": rfp, "textUpdates": kind("hrs_requirementtext"), "statusUpdates": kind("hrs_status"),
            "mandatoryUpdates": kind("hrs_mandatory"),
            "requirementFlags": [f for f in out["flags"] if "hrs_requirement@odata.bind" in f],
            "reviewFlags": [f for f in out["flags"] if "hrs_requirement@odata.bind" not in f],
            "addedCount": len(out["added"]), "rejected": validated["rejected"],
            "addedJson": json.dumps([{"reqId": a["req_id"], "section": a["section"], "text": a["text"]} for a in out["added"]])}


@route("match-rules", "MatchRules", "Candidate policy rules and threshold evidence per requirement (stage 6)")
def match_rules(body):
    rules = body["rules"]
    triggers = {r["ruleId"]: r.get("triggerPattern", "") for r in rules}
    thresholds = _thresholds(rules)
    out = []
    for req in body["requirements"]:
        cands = match_rule_candidates(req["text"], triggers, thresholds)
        results = {r.rule_id: r.to_dict() for r in evaluate_thresholds(req["text"], thresholds)}
        out.append({"reqId": req["reqId"], "candidates": [
            {**c.to_dict(), "threshold": results.get(c.rule_id)} for c in cands]})
    return {"results": out}


@route("answer-candidates", "AnswerCandidates", "Approved answer candidates per requirement (stage 5)")
def answer_candidates(body):
    library = _library(body["library"])
    top = int(body.get("top", 5))
    return {"results": [{"reqId": r["reqId"], "candidates": [c.to_dict() for c in
                         select_answer_candidates(r["text"], r["category"], library, top)]}
                        for r in body["requirements"]]}


@route("annotate-answers", "AnnotateAnswers", "Stale, conflict, retired and draft annotations for selected answers (stage 5)")
def annotate_answers(body):
    library = _library(body["library"])
    submission = _date(body["submissionDate"])
    return {"results": [{"reqId": s["reqId"], **annotate_answer(s["answerId"], s["text"], library, submission).to_dict()}
                        for s in body["selections"]]}


@route("check-certifications", "CheckCertifications", "Certificate validity on the submission date (stage 6)")
def certifications(body):
    certs = [Certification(c["certId"], c["name"], _date(c["issued"]), _date(c["expires"])) for c in body["certifications"]]
    return {"results": [vars(s) | {"expires": s.expires.isoformat()} for s in check_certifications(certs, _date(body["submissionDate"]))]}


@route("derive-compliance", "DeriveCompliance", "Compliance value per requirement from the precedence table (stage 6)")
def compliance(body):
    return {"results": [{"reqId": r["reqId"], "compliance": derive_compliance(
        status=r.get("status", "Active"), has_approved_answer=bool(r["hasApprovedAnswer"]), out_of_scope=bool(r.get("outOfScope")),
        fired_severities=r.get("firedSeverities", []), ambiguous=bool(r.get("ambiguous")),
        in_contradiction_group=bool(r.get("inContradictionGroup"))).value} for r in body["requirements"]]}


@route("route-sme", "RouteSme", "SME for each gap requirement (stage 7)")
def sme(body):
    primary = body["primarySmeByCategory"]
    return {"results": [{"reqId": g["reqId"], "smeId": route_sme(
        g["category"], [FiredRule(f["ruleId"], f["severity"], f["escalationSmeId"]) for f in g.get("firedRules", [])], primary)}
        for g in body["gaps"]]}


@route("derive-approval-level", "DeriveApprovalLevel", "Approval level from value band and flag severities (stage 9)")
def approval_level(body):
    level = derive_approval_level(body["valueBand"], body.get("flagSeverities", []),
                                  legal_exception=bool(body.get("legalException")),
                                  block_accepted_as_exception=bool(body.get("blockAcceptedAsException")))
    return {"approvalLevel": level.value}


@route("count-words", "CountWords", "Word counts under the buyer's counting rule")
def words(body):
    rule = body.get("countingRule", "excel_buyer_formula")
    return {"results": [{"key": t["key"], "words": count_words(t.get("text"), rule)} for t in body["texts"]]}


@route("map-response-codes", "MapResponseCodes", "Buyer response codes allowed for each compliance value")
def response_codes(body):
    codes = [(c["code"], c["meaning"]) for c in body["buyerCodes"]]
    return {"results": [{"reqId": r["reqId"], "allowedCodes": map_response_code(r["compliance"], codes)} for r in body["requirements"]]}


@route("fill-template", "FillTemplate", "Fill a Word template in place")
def fill(body):
    out = fill_template(_b64(body["templateBase64"]), body.get("scalars", {}), body.get("tables"), body.get("repeatingRows"))
    return {"contentBase64": _to_b64(out), "headings": heading1_titles(out)}


@route("split-sections", "SplitSections", "Split a proposal into governed narrative and excluded components")
def split(body):
    governed, excluded = split_sections(_b64(body["contentBase64"]), body["excludedHeadings"])
    return {"governedBase64": _to_b64(governed), "excludedBase64": _to_b64(excluded)}


@route("fill-compliance-matrix", "FillComplianceMatrix", "Fill the Excel compliance matrix template cell by cell")
def matrix(body):
    out = fill_compliance_matrix(_b64(body["templateBase64"]), body["rows"], body["buyerName"], body["rfpNumber"])
    return {"contentBase64": _to_b64(out)}


@route("count-pages", "CountPages", "Page count of a PDF (stage 8)")
def pages(body):
    return {"pages": count_pages(_b64(body["pdfBase64"]))}


@route("check-gates", "CheckGates", "Evaluate gates G4, G5 and G6")
def gates(body):
    out = {}
    if "g4" in body:
        out["G4"] = gate_g4(body["g4"]["requirements"]).to_dict()
    if "g5" in body:
        out["G5"] = gate_g5(body["g5"]["flags"], [tuple(k) for k in body["g5"]["exceptionKeys"]]).to_dict()
    if "g6" in body:
        g = body["g6"]
        out["G6"] = gate_g6(g.get("pageCount"), g.get("pageLimit"), g.get("wordCounts", {}), g.get("wordLimit"),
                            g.get("requiredForms", []), g.get("providedForms", []), g.get("unresolvedOcr", [])).to_dict()
    return {"gates": out, "passed": all(v["passed"] for v in out.values())}


@route("company-content", "CompanyContent", "Text of the approved company documents and the respondent scope statement")
def content(body):
    return company_content([(f["name"], _b64(f["contentBase64"])) for f in body["files"]])


@route("template-headings", "TemplateHeadings", "Heading 1 titles of a Word template")
def headings(body):
    return {"headings": heading1_titles(_b64(body["templateBase64"]))}


@route("match-opportunity", "MatchOpportunity", "The opportunity an inbound file belongs to, by RFP number")
def opportunity(body):
    return {"opportunityId": match_opportunity(body["fileName"], body["opportunities"])}


@route("certifications-mentioned", "CertificationsMentioned", "Register certificates each requirement names, valid on the submission date")
def certs_mentioned(body):
    certs = [Certification(c["certId"], c["name"], _date(c["issued"]), _date(c["expires"])) for c in body["certifications"]]
    return {"results": certifications_mentioned(body["requirements"], certs, _date(body["submissionDate"]))}


@route("render-card", "RenderCard", "Fill an Adaptive Card template's ${...} bindings")
def card(body):
    return {"card": render_card(body["template"], body["data"])}


@route("assemble-package", "AssemblePackage", "Stage 8: governed narrative, excluded components and compliance matrix")
def package(body):
    out = assemble_package(proposal_template=_b64(body["proposalTemplateBase64"]), matrix_template=_b64(body["matrixTemplateBase64"]),
                           scalars={**body["scalars"], **{s["token"]: s["text"] for s in body.get("sections", [])}}, requirements=body["requirements"], team=body.get("team", []),
                           excluded_headings=body.get("excludedHeadings", []), required_forms=_structured(body.get("requiredForms") or []))
    return {"governedBase64": _to_b64(out["governed"]), "excludedBase64": _to_b64(out["excluded"]),
            "matrixBase64": _to_b64(out["matrix"]), "exceptionKeys": out["exceptionKeys"], "formsChecklist": out["formsChecklist"],
            "accountedForms": [r["form"] for r in out["formsChecklist"]]}


@route("check-items", "CheckItems", "Validate a batch output: every expected ID once, allowed values only")
def check_items(body):
    """Prompts are trusted for judgment, never for bookkeeping: a missing, repeated or invented ID stops the stage."""
    id_field = body.get("idField", "reqId")
    expected = list(body["expectedIds"])
    ids = [i.get(id_field) for i in body["items"]]
    allowed = body.get("allowed", {})
    invalid = [f"{i.get(id_field)}.{field}={i.get(field)!r}" for i in body["items"] for field, values in allowed.items()
               if i.get(field) not in values and not (i.get(field) == "" and "" in values)]
    missing = sorted(set(expected) - set(ids))
    unexpected = sorted(set(ids) - set(expected))
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    ok = not (missing or unexpected or duplicates or invalid)
    return {"ok": ok, "missing": missing, "unexpected": unexpected, "duplicates": duplicates, "invalid": invalid}


# Stage operations: thin JSON wrappers over rfp_tools.stages. Body keys are the function's
# parameters in camelCase; the response is the function's return value.
_STAGE_OPS = [
    ("stage-block-rows", "StageBlockRows", "Stage 1: hrs_documentblock rows from normalized blocks", stages.block_rows, ["blocks", "documentId"]),
    ("stage-rfp-facts", "StageRfpFacts", "Stage 1: hrs_rfp columns from the facts prompt", stages.rfp_facts_update, ["facts", "opportunity"]),
    ("stage-taxonomy", "StageTaxonomy", "Stage 2: taxonomy text for the classification prompt", stages.taxonomy_text, ["categories"]),
    ("stage-requirement-rows", "StageRequirementRows", "Stage 2: hrs_requirement rows from extraction and classification",
     stages.requirement_rows, ["rfpId", "documentId", "requirements", "classification", "categories", "revision"]),
    ("stage-ocr-flags", "StageOcrFlags", "Stage 2: OCR-LOW-CONFIDENCE flags for requirements read below the threshold",
     stages.ocr_flag_rows, ["rfpId", "requirements", "lowConfidence"]),
    ("stage-added-flags", "StageAddedFlags", "Stage 3: ADDENDUM-CHANGE flags for requirements an addendum added",
     addendum.added_flag_rows, ["rfpId", "revision", "requirements", "added"]),
    ("stage-prompt-requirements", "StagePromptRequirements", "Requirement rows as compact prompt JSON", stages.prompt_requirements, ["requirements"]),
    ("orchestrator-run-status", "OrchestratorRunStatus", "RFP Orchestrator: an RFP's stage, waits and recent run log",
     orchestrator.run_status, ["rfp", "runlogs", "gaps", "ocrOpen", "approvals"]),
    ("orchestrator-explain-flags", "OrchestratorExplainFlags", "RFP Orchestrator: the flags on one requirement and why",
     orchestrator.explain_flags, ["requirement", "flags"]),
    ("orchestrator-explain-answer", "OrchestratorExplainAnswer", "RFP Orchestrator: a requirement's compliance, answer ID and response",
     orchestrator.explain_answer, ["requirement", "responses", "gaps"]),
    ("orchestrator-rerun-check", "OrchestratorRerunCheck", "RFP Orchestrator: whether a stage may be rerun now",
     orchestrator.rerun_check, ["rfp", "stage"]),
    ("stage-classify-batches", "StageClassifyBatches", "Stage 2: classification prompt inputs in batches", stages.classify_batches, ["requirements"]),
    ("stage-relation-batches", "StageRelationBatches", "Stage 4: relations prompt inputs, grouped and batched above the pilot size",
     stages.relation_batches, ["requirements"]),
    ("stage-relation-updates", "StageRelationUpdates", "Stage 4: requirement updates and flags from the relations prompt",
     stages.relation_updates, ["rfpId", "requirements", "relations", "relations2", "relations3"]),
    ("stage-shortlist-items", "StageShortlistItems", "Stage 5: approved question index and requirements for the shortlist prompt",
     stages.shortlist_items, ["requirements", "answers"]),
    ("stage-selection-items", "StageSelectionItems", "Stage 5: approved candidates per requirement for the selection prompt",
     stages.selection_items, ["requirements", "answers", "shortlist"]),
    ("stage-resolve-selections", "StageResolveSelections", "Stage 5: validated selections to responses, library flags and gaps",
     stages.resolve_selections, ["rfpId", "requirements", "answers", "items", "selections", "submissionDate", "shortlist", "selections2"]),
    ("stage-rule-screen-items", "StageRuleScreenItems", "Stage 6: rule register and requirements for the screen prompt",
     stages.rule_screen_items, ["requirements", "rules"]),
    ("stage-adjudication-items", "StageAdjudicationItems", "Stage 6: candidate rule hits with threshold evidence",
     stages.adjudication_items, ["requirements", "rules", "certifications", "submissionDate", "screen"]),
    ("stage-resolve-policy", "StageResolvePolicy", "Stage 6: policy flags, compliance and drafting items",
     stages.resolve_policy, ["rfpId", "requirements", "rules", "certifications", "responses", "items", "adjudications", "mentions", "buyerCodes"]),
    ("stage-resolve-drafts", "StageResolveDrafts", "Stage 6: validated drafts to response updates",
     stages.resolve_drafts, ["draftItems", "drafts", "wordLimit", "countingRule"]),
    ("stage-gap-rows", "StageGapRows", "Stage 7: routed hrs_gap rows",
     stages.gap_rows, ["requirements", "responses", "flags", "rules", "categories", "dueBy", "rfpId", "proposals", "rfpKey"]),
    ("stage-clarification-items", "StageClarificationItems", "Stage 7: items for the clarification prompt",
     stages.clarification_items, ["requirements", "flags"]),
    ("stage-resolve-clarifications", "StageResolveClarifications", "Stage 7: clarification rows and letter rows",
     stages.resolve_clarifications, ["rfpId", "items", "drafted"]),
    ("stage-section-inputs", "StageSectionInputs", "Stage 8: inputs for each narrative section prompt",
     stages.section_inputs, ["requirements", "responses", "opportunity", "budgetScale"]),
    ("stage-package-requirements", "StagePackageRequirements", "Stage 8: requirement rows for package assembly",
     stages.package_requirements, ["requirements", "responses", "flags", "rules", "gaps"]),
    ("stage-approval-summary", "StageApprovalSummary", "Stage 9: counts and exceptions for the approval card",
     stages.approval_summary, ["requirements", "gaps", "clarifications", "packageRows"]),
    ("stage-gate-inputs", "StageGateInputs", "Stage 8: inputs for gates G5 and G6",
     stages.gate_inputs, ["packageRows", "responses", "wordLimit"]),
]


def _snake(name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() else c for c in name)


def _structured(value):
    """Flows can pass an array or object as JSON text (an @{...} interpolation); accept it as data."""
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _stage_handler(fn, params):
    def handler(body):
        kwargs = {_snake(p): _structured(body[p]) for p in params if p in body}
        result = fn(**kwargs)
        return result if isinstance(result, dict) else {"result": result}
    return handler


for _route, _op, _summary, _fn, _params in _STAGE_OPS:
    route(_route, _op, _summary)(_stage_handler(_fn, _params))


@app.route(route="{operation}", methods=["POST"])
def dispatch(req: func.HttpRequest) -> func.HttpResponse:
    operation = req.route_params.get("operation", "")
    handler = ROUTES.get(operation)
    if handler is None:
        return func.HttpResponse(json.dumps({"error": f"unknown operation {operation!r}"}), status_code=404, mimetype="application/json")
    try:
        _inject_fault(operation)
        result = handler(req.get_json())
        return func.HttpResponse(json.dumps(result), mimetype="application/json")
    except TransientError as e:
        # Throttling or a brief outage downstream: 503 is retried by the flow's retry policy (ADR-0026).
        logging.warning("rfp-tools %s transient failure: %s", operation, e)
        return func.HttpResponse(json.dumps({"error": f"TransientError: {e}"}), status_code=503, mimetype="application/json",
                                 headers={"Retry-After": "10"})
    except (KeyError, ValueError, TypeError) as e:
        logging.warning("rfp-tools %s rejected input: %s", operation, e)
        return func.HttpResponse(json.dumps({"error": f"{type(e).__name__}: {e}"}), status_code=400, mimetype="application/json")


_fault_calls: dict[str, int] = {}


def _inject_fault(operation: str) -> None:
    """Failure drill (a), dev only: app setting RFPTOOLS_FAULTS="<operation>:<n>" fails the first n calls of that
    operation on this instance with a TransientError. Unset everywhere except during a drill."""
    spec = os.environ.get("RFPTOOLS_FAULTS", "")
    for item in filter(None, (s.strip() for s in spec.split(","))):
        name, _, count = item.partition(":")
        if name == operation and _fault_calls.get(name, 0) < int(count or 1):
            _fault_calls[name] = _fault_calls.get(name, 0) + 1
            raise TransientError(f"injected fault {_fault_calls[name]} of {count or 1} for {operation} (RFPTOOLS_FAULTS)")


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(json.dumps({"status": "ok", "operations": sorted(ROUTES)}), mimetype="application/json")
