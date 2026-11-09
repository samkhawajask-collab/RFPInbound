"""Plumbing test: every stage function in pipeline order on Dataverse-shaped rows and scripted prompt output.

The rows are the reference data given synthetic GUIDs, shaped as the Dataverse connector returns them.
Prompt outputs are scripted, so this checks joins, choice mapping and validation - not model judgment.
No gold data is read.
"""

import json
import uuid

import pytest

from rfp_tools.documents import normalize_docx
from rfp_tools.requirements import assign_requirement_ids, extract_table_requirements
from rfp_tools.schema import choice_label, choice_value
from rfp_tools import stages
from tests.conftest import REPO_ROOT, read_reference_csv

RFP_A = REPO_ROOT / "data" / "RFP_Synthetic_Dataset_Pilot" / "01_Inbound_RFPs" / "RFP-A_Cedar_Valley_Water_Authority_RFP-2026-117.docx"
RFP_ID, DOC_ID = str(uuid.uuid4()), str(uuid.uuid4())


def gid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(scope="module")
def ref():
    smes = {r["hrs_smeid"]: r for r in read_reference_csv("02_hrs_sme.csv")}
    categories = [{"hrs_categoryid": gid(), "hrs_categorycode": c["hrs_categorycode"], "hrs_name": c["hrs_name"],
                   "hrs_description": c["hrs_description"], "hrs_primarysmeid": {"hrs_smecode": c["hrs_primarysmeid"]}}
                  for c in read_reference_csv("01_hrs_category.csv")]
    answers = [{"hrs_answerid": gid(), "hrs_answercode": a["hrs_answerid"], "hrs_question": a["hrs_question"],
                "hrs_answertext": a["hrs_answertext"], "hrs_categorycode": {"hrs_categorycode": a["hrs_categorycode"]},
                "hrs_approvalstatus": choice_value("hrs_approvalstatus", a["hrs_approvalstatus"]),
                "hrs_lastreviewed": a["hrs_lastreviewed"], "hrs_confidentiality": choice_value("hrs_confidentiality", a["hrs_confidentiality"])}
               for a in read_reference_csv("03_hrs_answer.csv")]
    import csv
    with open(REPO_ROOT / "data" / "supplement" / "policy_rule_thresholds.csv", encoding="utf-8") as f:
        th = {r["hrs_ruleid"]: r for r in csv.DictReader(f)}
    rules = [{"hrs_policyruleid": gid(), "hrs_ruleid": r["hrs_ruleid"], "hrs_triggerpattern": r["hrs_triggerpattern"],
              "hrs_condition": r["hrs_condition"], "hrs_severity": choice_value("hrs_severity", r["hrs_severity"]),
              "hrs_approvedposition": r["hrs_approvedposition"], "hrs_escalationsmeid": {"hrs_smecode": r["hrs_escalationsmeid"]},
              **({"hrs_thresholdmetric": th[r["hrs_ruleid"]]["hrs_thresholdmetric"], "hrs_thresholdoperator": th[r["hrs_ruleid"]]["hrs_thresholdoperator"],
                  "hrs_thresholdvalue": float(th[r["hrs_ruleid"]]["hrs_thresholdvalue"])} if r["hrs_ruleid"] in th else {})}
             for r in read_reference_csv("04_hrs_policyrule.csv")]
    certs = [{"hrs_certificationid": gid(), "hrs_certid": c["hrs_certid"], "hrs_name": c["hrs_name"], "hrs_issued": c["hrs_issued"],
              "hrs_expires": c["hrs_expires"]} for c in read_reference_csv("07_hrs_certification.csv")]
    return {"smes": smes, "categories": categories, "answers": answers, "rules": rules, "certs": certs}


def test_pipeline_plumbing(ref):
    blocks = normalize_docx(RFP_A.read_bytes())
    rows = stages.block_rows([b.to_dict() for b in blocks], DOC_ID)
    assert len(rows) == len(blocks) and all(r["hrs_rfpdocument@odata.bind"].endswith(f"({DOC_ID})") for r in rows)

    extracted = [c.to_dict() for c in assign_requirement_ids(extract_table_requirements(blocks), "CV")]
    classification = {"items": [{"reqId": r["req_id"], "categoryCode": "WORK", "confidence": 0.9} for r in extracted]}
    stage2 = stages.requirement_rows(RFP_ID, DOC_ID, extracted, classification, ref["categories"])
    assert not stage2["problems"] and len(stage2["rows"]) == 60
    assert stage2["rows"][0]["hrs_categorycode@odata.bind"] == "hrs_categories(hrs_categorycode='WORK')"

    # Dataverse now holds the requirements; give them IDs and expanded category as List rows returns them.
    reqs = [{"hrs_requirementid": gid(), "hrs_reqid": r["hrs_reqid"], "hrs_requirementtext": r["hrs_requirementtext"],
             "hrs_section": r["hrs_section"], "hrs_location": r["hrs_location"], "hrs_status": r["hrs_status"],
             "hrs_mandatory": r.get("hrs_mandatory"), "hrs_categorycode": {"hrs_categorycode": "COMP"}} for r in stage2["rows"]]
    by = {r["hrs_reqid"]: r for r in reqs}

    rel = stages.relation_updates(RFP_ID, reqs, {
        "duplicates": [{"reqId": "CV-059", "duplicateOf": "CV-002", "reason": "same"}],
        "contradictions": [{"reqIds": ["CV-037", "CV-039"], "reason": "hosting"}],
        "ambiguous": [{"reqId": "CV-038", "reason": "vague"}], "outOfScope": [{"reqId": "CV-013", "reason": "hardware"}]})
    assert not rel["problems"] and len(rel["flags"]) == 5
    for u in rel["updates"]:
        r = by[u["reqId"]]
        r.update({k: v for k, v in u["fields"].items() if "@" not in k})
        if "hrs_duplicateof@odata.bind" in u["fields"]:
            r["_hrs_duplicateof_value"] = by["CV-002"]["hrs_requirementid"]

    sel_in = stages.selection_items(reqs, ref["answers"])
    assert sel_in["count"] == 59  # the duplicate is not answered separately
    approved = {a["hrs_answercode"] for a in ref["answers"] if choice_label("hrs_approvalstatus", a["hrs_approvalstatus"]) == "Approved"}
    assert all(c["answerId"] in approved for i in sel_in["items"] for c in i["candidates"])
    selections = {"items": [{"reqId": i["reqId"], "answerId": i["candidates"][0]["answerId"] if i["candidates"] and i["reqId"] != "CV-001" else "",
                             "reason": "r"} for i in sel_in["items"]]}
    sel = stages.resolve_selections(RFP_ID, reqs, ref["answers"], sel_in["items"], selections, "2026-11-20")
    assert not sel["problems"] and "CV-001" in sel["gaps"]
    assert all("hrs_answer@odata.bind" in r for r in sel["responses"])
    cites = {r["hrs_requirement@odata.bind"]: r["hrs_answer@odata.bind"] for r in sel["responses"]}
    dup = lambda rid: f"hrs_requirements({by[rid]['hrs_requirementid']})"
    assert cites[dup("CV-059")] == cites[dup("CV-002")]  # the duplicate cites its original's answer

    responses = [{"hrs_responseid": gid(), "_hrs_requirement_value": r["hrs_requirement@odata.bind"][len("hrs_requirements("):-1],
                  "hrs_smereviewrequired": r["hrs_smereviewrequired"], "hrs_responsetext": r.get("hrs_responsetext"),
                  "hrs_answer": {"hrs_answercode": r["hrs_answer@odata.bind"], "hrs_question": "q", "hrs_answertext": "a"}} for r in sel["responses"]]

    adj_in = stages.adjudication_items(reqs, ref["rules"], ref["certs"], "2026-11-20")
    assert adj_in["count"] > 0 and "NOT valid" in adj_in["certificationsText"]
    adjudications = {"items": [{"reqId": i["reqId"], "ruleId": i["ruleId"], "fired": bool(i["thresholdEvidence"] and i["thresholdEvidence"]["breached"]),
                                "reason": "r"} for i in adj_in["items"]]}
    codes = [{"code": "F", "meaning": "Fully compliant"}, {"code": "P", "meaning": "Partially compliant"}, {"code": "N", "meaning": "Not compliant"}]
    pol = stages.resolve_policy(RFP_ID, reqs, ref["rules"], ref["certs"], responses, adj_in["items"], adjudications, adj_in["mentions"], codes)
    assert not pol["problems"]
    compliance = {u["reqId"]: choice_label("hrs_compliance", u["fields"]["hrs_compliance"]) for u in pol["complianceUpdates"]}
    assert len(compliance) == 60  # every requirement, duplicates included (gate G4)
    assert compliance["CV-013"] == "No" and compliance["CV-001"] == "Gap" and compliance["CV-038"] == "Clarify"
    assert compliance["CV-059"] == compliance["CV-002"]

    drafts = {"items": [{"reqId": i["reqId"], "responseCode": i["allowedCodes"][0], "responseText": "We comply."} for i in pol["draftItems"]]}
    dr = stages.resolve_drafts(pol["draftItems"], drafts, None, "whitespace")
    assert not dr["problems"] and all(u["fields"]["hrs_wordcount"] == 2 for u in dr["updates"])

    # An RFP with no response codes of its own: the compliance value is the code.
    no_codes = stages.resolve_policy(RFP_ID, reqs, ref["rules"], ref["certs"], responses, adj_in["items"], adjudications, adj_in["mentions"], [])
    assert all(i["allowedCodes"] == [i["compliance"]] for i in no_codes["draftItems"])

    for u in pol["complianceUpdates"]:
        by[u["reqId"]]["hrs_compliance"] = u["fields"]["hrs_compliance"]
    flags = [{"_hrs_requirement_value": f["hrs_requirement@odata.bind"][len("hrs_requirements("):-1], "hrs_flagtype": f["hrs_flagtype"],
              "hrs_detail": f.get("hrs_detail"), "_hrs_policyrule_value": f.get("hrs_policyrule@odata.bind", "")[len("hrs_policyrules("):-1] or None}
             for f in rel["flags"] + sel["flags"] + pol["flags"]]
    gaps = stages.gap_rows(reqs, responses, flags, ref["rules"], ref["categories"], "2026-11-01T00:00:00Z", RFP_ID)
    gap_ids = {g["reqId"] for g in gaps["rows"]}
    assert gap_ids == {r for r, c in compliance.items() if c == "Gap" and not by[r].get("_hrs_duplicateof_value")}
    assert "CV-013" not in gap_ids  # unanswered but out of scope: No, not a gap
    assert all(g["smeCode"].startswith("SME-") for g in gaps["rows"])
    assert {f["hrs_name"].split()[-1] for f in gaps["flags"]} == gap_ids

    # ADR-0033: an answer to the same text is reused without a card; a changed text or an empty answer asks again.
    ask, same, changed = sorted(gap_ids)[:3]
    proposals = [{"hrs_answercode": f"PROP-A-{same}", "hrs_answerid": gid(), "hrs_question": by[same]["hrs_requirementtext"], "hrs_answertext": "SME text"},
                 {"hrs_answercode": f"PROP-A-{changed}", "hrs_answerid": gid(), "hrs_question": "Older wording", "hrs_answertext": "SME text"},
                 {"hrs_answercode": f"PROP-A-{ask}", "hrs_answerid": gid(), "hrs_question": by[ask]["hrs_requirementtext"], "hrs_answertext": "  "}]
    again = stages.gap_rows(reqs, responses, flags, ref["rules"], ref["categories"], "2026-11-01T00:00:00Z", RFP_ID, proposals, "A")
    assert [g["reqId"] for g in again["reusedRows"]] == [same] and again["count"] == gaps["count"] - 1
    assert {ask, changed} <= {g["reqId"] for g in again["rows"]}
    reused = again["reusedRows"][0]
    assert reused["hrs_smestatus"] == choice_value("hrs_smestatus", "Answered") and reused["hrs_smeanswertext"] == "SME text"
    assert reused["hrs_proposedanswer@odata.bind"] == f"hrs_answers({proposals[0]['hrs_answerid']})"
    assert "reused" in next(f for f in again["flags"] if f["hrs_name"].endswith(same))["hrs_detail"]
    other_rfp = stages.gap_rows(reqs, responses, flags, ref["rules"], ref["categories"], "2026-11-01T00:00:00Z", RFP_ID, proposals, "B")
    assert other_rfp["reusedRows"] == []

    clar = stages.clarification_items(reqs, flags)
    assert {i["trigger"] for i in clar["items"]} == {"Ambiguity", "Contradiction"}
    letter = stages.resolve_clarifications(RFP_ID, clar["items"], {"items": [{"key": i["key"], "questionText": "Q?"} for i in clar["items"]]})
    assert not letter["problems"] and len(letter["letterRows"]) == 2

    pkg = stages.package_requirements(reqs, [{**r, "hrs_responsetext": r.get("hrs_responsetext") or "We comply.", "hrs_responsecode": "F"}
                                             for r in responses], flags,
                                      ref["rules"], [{"_hrs_requirement_value": by["CV-001"]["hrs_requirementid"], "hrs_smeanswertext": "SME text"}])
    assert len(pkg) == 60 and next(p for p in pkg if p["reqId"] == "CV-001")["smeAnswer"] == "SME text"
    assert "See the response to CV-002" in next(p for p in pkg if p["reqId"] == "CV-059")["responseText"]
    summary = stages.approval_summary(reqs, gaps["rows"], letter["rows"], pkg)
    assert summary["totalRequirements"] == 60


def test_bad_prompt_output_is_reported(ref):
    extracted = [{"req_id": "X-1", "text": "t", "mandatory": "Mandatory", "section": "4", "location": "L", "source_block_index": 1}]
    out = stages.requirement_rows(RFP_ID, DOC_ID, extracted, {"items": [{"reqId": "X-9", "categoryCode": "NOPE", "confidence": 1}]}, ref["categories"])
    assert out["rows"] == [] and len(out["problems"]) == 3  # missing X-1, unknown X-9, unknown category

    reqs = [{"hrs_requirementid": gid(), "hrs_reqid": "X-1", "hrs_requirementtext": "State your RPO.", "hrs_status": choice_value("hrs_requirementstatus", "Active"),
             "hrs_categorycode": {"hrs_categorycode": "AVAIL"}}]
    items = stages.selection_items(reqs, ref["answers"])["items"]
    bad = stages.resolve_selections(RFP_ID, reqs, ref["answers"], items, {"items": [{"reqId": "X-1", "answerId": "ANS-9999", "reason": ""}]}, "2026-11-20")
    assert bad["responses"] == [] and "not one of its candidates" in bad["problems"][0]

    draft_items = [{"reqId": "X-1", "responseId": gid(), "allowedCodes": ["F"], "flags": []}]
    bad_codes = stages.resolve_drafts(draft_items, {"items": [{"reqId": "X-1", "responseCode": "Z", "responseText": "x"}]}, None, "whitespace")
    assert bad_codes["updates"] == [] and "used code" in bad_codes["problems"][0]


def test_facts_dates_are_normalized_in_code():
    row = stages.rfp_facts_update({"dueDate": "2026-11-20", "qaDeadline": "2026-11-02 17:00 ET", "responseCodes": []},
                                  {"hrs_duedate": "2026-11-21", "hrs_qadeadline": "2026-11-03"})
    assert row["hrs_duedate"] == "2026-11-20" and row["hrs_qadeadline"] == "2026-11-02"
    fallback = stages.rfp_facts_update({"dueDate": "", "qaDeadline": "November 2", "responseCodes": []},
                                       {"hrs_duedate": "2026-11-21", "hrs_qadeadline": "2026-11-03T00:00:00Z"})
    assert fallback["hrs_duedate"] == "2026-11-21" and fallback["hrs_qadeadline"] == "2026-11-03"


def test_shortlist_widens_candidates_across_categories(ref):
    """A meaning-based shortlist adds Approved answers from other categories; Retired, Draft and unknown IDs are dropped."""
    approved = [a for a in ref["answers"] if choice_label("hrs_approvalstatus", a["hrs_approvalstatus"]) == "Approved"]
    draft = next(a for a in ref["answers"] if choice_label("hrs_approvalstatus", a["hrs_approvalstatus"]) == "Draft")
    other = next(a for a in approved if a["hrs_categorycode"]["hrs_categorycode"] != "COMP")
    req = {"hrs_requirementid": gid(), "hrs_reqid": "X-1", "hrs_requirementtext": "Unrelated wording entirely",
           "hrs_status": choice_value("hrs_requirementstatus", "Active"), "hrs_categorycode": {"hrs_categorycode": "COMP"}}
    sl = stages.shortlist_items([req], ref["answers"])
    approved_part, unusable_part = sl["index"].split("### unusable list")
    assert draft["hrs_answercode"] not in approved_part and draft["hrs_answercode"] in unusable_part
    assert other["hrs_answercode"] in approved_part
    shortlist = {"items": [{"reqId": "X-1", "answerIds": [other["hrs_answercode"], draft["hrs_answercode"], "ANS-9999"]}]}
    cands = [c["answerId"] for c in stages.selection_items([req], ref["answers"], shortlist)["items"][0]["candidates"]]
    assert cands[0] == other["hrs_answercode"] and draft["hrs_answercode"] not in cands and "ANS-9999" not in cands


def test_gap_with_only_a_draft_raises_draft_present(ref):
    draft = next(a for a in ref["answers"] if choice_label("hrs_approvalstatus", a["hrs_approvalstatus"]) == "Draft")
    req = {"hrs_requirementid": gid(), "hrs_reqid": "X-1", "hrs_requirementtext": draft["hrs_question"],
           "hrs_status": choice_value("hrs_requirementstatus", "Active"), "hrs_categorycode": {"hrs_categorycode": "COMP"}}
    items = stages.selection_items([req], ref["answers"])["items"]
    sel = stages.resolve_selections(RFP_ID, [req], ref["answers"], items, {"items": [{"reqId": "X-1", "answerId": "", "reason": "none"}]}, "2026-11-20")
    types = {choice_label("hrs_flagtype", f["hrs_flagtype"]) for f in sel["flags"]}
    assert types == {"LIBRARY-DRAFT-PRESENT"}  # NO-LIBRARY-ANSWER is raised in stage 7, once compliance says Gap


def test_retired_match_uses_superseded_by_link():
    from datetime import date
    from rfp_tools.library import Answer, annotate_answer
    lib = [Answer("A-1", "Hosting model?", "Cloud.", "TECH", "Approved", date(2026, 1, 1)),
           Answer("A-0", "Completely different wording", "Old.", "TECH", "Retired", date(2020, 1, 1), superseded_by="A-1")]
    assert annotate_answer("A-1", "Where is it hosted?", lib, date(2026, 11, 20)).retired_match_ids == ["A-0"]


def test_rule_screen_adds_candidates_for_adjudication(ref):
    req = {"hrs_requirementid": gid(), "hrs_reqid": "X-1", "hrs_requirementtext": "Servers shall sit in the buyer's own building.",
           "hrs_status": choice_value("hrs_requirementstatus", "Active"), "hrs_categorycode": {"hrs_categorycode": "TECH"}}
    screen_in = stages.rule_screen_items([req], ref["rules"])
    assert screen_in["index"].startswith("R-01 [") and screen_in["count"] == 1
    plain = stages.adjudication_items([req], ref["rules"], ref["certs"], "2026-11-20")
    screened = stages.adjudication_items([req], ref["rules"], ref["certs"], "2026-11-20",
                                         {"items": [{"reqId": "X-1", "ruleIds": ["R-09", "R-99"]}]})
    assert {i["ruleId"] for i in screened["items"]} - {i["ruleId"] for i in plain["items"]} == {"R-09"}  # unknown R-99 dropped


def test_shortlist_unusable_ids_only_add_flags(ref):
    """unusableIds raise RETIRED/DRAFT flags for a gap; an Approved ID smuggled into that list is ignored."""
    lab = lambda a: choice_label("hrs_approvalstatus", a["hrs_approvalstatus"])
    retired = next(a for a in ref["answers"] if lab(a) == "Retired")
    approved = next(a for a in ref["answers"] if lab(a) == "Approved")
    req = {"hrs_requirementid": gid(), "hrs_reqid": "X-1", "hrs_requirementtext": "Unrelated wording entirely",
           "hrs_status": choice_value("hrs_requirementstatus", "Active"), "hrs_categorycode": {"hrs_categorycode": "COMP"}}
    sl_in = stages.shortlist_items([req], ref["answers"])
    assert retired["hrs_answercode"] in sl_in["unusable"] and retired["hrs_answercode"] not in sl_in["index"].split("### unusable list")[0]
    shortlist = {"items": [{"reqId": "X-1", "answerIds": [], "unusableIds": [retired["hrs_answercode"], approved["hrs_answercode"]]}]}
    items = stages.selection_items([req], ref["answers"], shortlist)["items"]
    sel = stages.resolve_selections(RFP_ID, [req], ref["answers"], items, {"items": [{"reqId": "X-1", "answerId": "", "reason": ""}]},
                                    "2026-11-20", shortlist)
    flag = next(f for f in sel["flags"] if choice_label("hrs_flagtype", f["hrs_flagtype"]) == "LIBRARY-RETIRED-MATCH")
    assert retired["hrs_answercode"] in flag["hrs_detail"] and approved["hrs_answercode"] not in flag["hrs_detail"]


def test_agree_relations_keeps_only_shared_findings():
    first = {"duplicates": [{"reqId": "X-2", "duplicateOf": "X-1", "reason": "a"}, {"reqId": "X-4", "duplicateOf": "X-3", "reason": "b"}],
             "contradictions": [{"reqIds": ["X-5", "X-6", "X-7"], "reason": "c"}],
             "ambiguous": [{"reqId": "X-8", "reason": "d"}], "outOfScope": [{"reqId": "X-9", "reason": "e"}]}
    second = {"duplicates": [{"reqId": "X-2", "duplicateOf": "X-1", "reason": "z"}],
              "contradictions": [{"reqIds": ["X-6", "X-5"], "reason": "y"}], "ambiguous": [], "outOfScope": [{"reqId": "X-9", "reason": "x"}]}
    agreed = stages.agree_relations(first, second)
    assert [d["reqId"] for d in agreed["duplicates"]] == ["X-2"] and agreed["duplicates"][0]["reason"] == "a"
    assert agreed["contradictions"][0]["reqIds"] == ["X-5", "X-6"]
    assert agreed["ambiguous"] == [] and [o["reqId"] for o in agreed["outOfScope"]] == ["X-9"]
    assert stages.agree_relations(first, None) == first


def test_agree_relations_with_three_passes_keeps_majority_findings():
    first = {"duplicates": [{"reqId": "X-2", "duplicateOf": "X-1", "reason": "a"}],
             "contradictions": [{"reqIds": ["X-5", "X-6"], "reason": "c"}],
             "ambiguous": [{"reqId": "X-8", "reason": "d"}, {"reqId": "X-8", "reason": "d again"}], "outOfScope": [{"reqId": "X-9", "reason": "e"}]}
    second = {"duplicates": [{"reqId": "X-4", "duplicateOf": "X-3", "reason": "b"}],
              "contradictions": [], "ambiguous": [], "outOfScope": []}
    third = {"duplicates": [{"reqId": "X-4", "duplicateOf": "X-3", "reason": "z"}, {"reqId": "X-2", "duplicateOf": "X-1", "reason": "y"}],
             "contradictions": [{"reqIds": ["X-6", "X-5", "X-7"], "reason": "x"}], "ambiguous": [], "outOfScope": []}
    agreed = stages.agree_relations(first, second, third)
    # Two of three: X-2 (passes 1 and 3) and X-4 (passes 2 and 3), each with the first reporting pass's reason.
    assert {(d["reqId"], d["reason"]) for d in agreed["duplicates"]} == {("X-2", "a"), ("X-4", "b")}
    assert agreed["contradictions"] == [{"reqIds": ["X-5", "X-6"], "reason": "c"}]
    # One pass only, even when that pass lists it twice.
    assert agreed["ambiguous"] == [] and agreed["outOfScope"] == []
    # A group the first pass lacks comes from the next pass that has it.
    agreed = stages.agree_relations({"duplicates": [], "contradictions": [], "ambiguous": [], "outOfScope": []}, third, third)
    assert agreed["contradictions"] == [{"reqIds": ["X-6", "X-5", "X-7"], "reason": "x"}]


def test_agree_selections_needs_both_passes_to_find_an_answer():
    first = {"items": [{"reqId": "X-1", "answerId": "A-1", "reason": ""}, {"reqId": "X-2", "answerId": "A-2", "reason": ""},
                       {"reqId": "X-3", "answerId": "", "reason": ""}, {"reqId": "X-4", "answerId": "A-4", "reason": ""}]}
    second = {"items": [{"reqId": "X-1", "answerId": "A-1"}, {"reqId": "X-2", "answerId": ""},
                        {"reqId": "X-3", "answerId": "A-3"}, {"reqId": "X-4", "answerId": "A-5"}]}
    got = {s["reqId"]: s["answerId"] for s in stages.agree_selections(first, second)["items"]}
    assert got == {"X-1": "A-1", "X-2": "", "X-3": "", "X-4": "A-4"}


def test_drafts_merge_across_batches():
    items = [{"reqId": f"X-{n}", "responseId": gid(), "allowedCodes": ["F"], "flags": []} for n in range(1, 4)]
    batches = [{"items": [{"reqId": "X-1", "responseCode": "F", "responseText": "One two."}]},
               {"items": [{"reqId": "X-2", "responseCode": "F", "responseText": "Three."}, {"reqId": "X-3", "responseCode": "F", "responseText": "Four."}]}]
    out = stages.resolve_drafts(items, batches, None, "whitespace")
    assert not out["problems"] and len(out["updates"]) == 3
    missing = stages.resolve_drafts(items, batches[:1], None, "whitespace")
    assert any("no result" in p for p in missing["problems"])  # gate still catches a short batch


def test_stage_5_batches_and_merges_batch_outputs():
    items = [{"reqId": f"X-{n}"} for n in range(1, 46)]
    batches = stages._batches(items, stages.SELECT_BATCH)
    assert [len(json.loads(b)) for b in batches] == [20, 20, 5]
    merged = stages._merged([{"items": [{"reqId": "X-1"}]}, None, {"items": [{"reqId": "X-2"}, {"reqId": "X-3"}]}])
    assert [i["reqId"] for i in merged["items"]] == ["X-1", "X-2", "X-3"]
    assert stages._merged({"items": [{"reqId": "X-9"}]}) == {"items": [{"reqId": "X-9"}]} and stages._merged(None) == {"items": []}
