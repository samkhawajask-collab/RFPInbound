"""Ten times the design volume (ARCHITECTURE.md 14.1, ADR-0029): the whole-set prompts split only above the pilot's
largest single call, batch outputs merge back, and the code-side stage operations stay fast at 500 requirements
against a 2,000-answer library. Synthetic rows only; no gold data is read."""

import json
import time
import uuid

from rfp_tools import stages
from rfp_tools.requirements import resolve_identified
from rfp_tools.schema import choice_value


def req_rows(n: int, categories=("SEC", "TECH", "IMPL", "SUPP", "AVAIL")) -> list[dict]:
    return [{"hrs_requirementid": str(uuid.uuid4()), "hrs_reqid": f"SCL-{i:03d}", "hrs_section": f"Section {i // 25}",
             "hrs_requirementtext": f"The vendor shall provide capability number {i} for {categories[i % len(categories)]}.",
             "hrs_status": choice_value("hrs_requirementstatus", "Active"),
             "hrs_categorycode": {"hrs_categorycode": categories[i % len(categories)]}} for i in range(1, n + 1)]


def test_pilot_sizes_stay_one_call():
    """RFP B is the pilot's largest: 90 requirements, 119 candidates, 92 screened, 17 hits, 3 clarifications."""
    reqs = [{"req_id": f"B-{i}", "section": "S", "text": "t"} for i in range(90)]
    assert len(stages.classify_batches(reqs)["batches"]) == 1
    assert len(stages.relation_batches(req_rows(92))["batches"]) == 1
    assert stages.relation_batches(req_rows(92))["batches"][0] == stages.prompt_requirements(req_rows(92))
    assert stages.IDENTIFY_BATCH >= 119 and stages.SCREEN_BATCH >= 92 and stages.ADJUDICATE_BATCH >= 17 and stages.CLARIFY_BATCH >= 3


def test_five_hundred_requirements_split_and_group_by_category():
    reqs = [{"req_id": f"SCL-{i}", "section": "S", "text": "t"} for i in range(500)]
    batches = stages.classify_batches(reqs)["batches"]
    assert len(batches) == 6 and sum(len(json.loads(b)) for b in batches) == 500
    rel = stages.relation_batches(req_rows(500))["batches"]
    parsed = [json.loads(b) for b in rel]
    assert all(len(b) <= stages.RELATIONS_BATCH for b in parsed) and sum(map(len, parsed)) == 500
    # 100 requirements per category: each batch holds exactly one category.
    for b in parsed:
        assert len({int(r["reqId"].split("-")[1]) % 5 for r in b}) == 1


def test_batch_outputs_merge_for_every_batched_prompt():
    rows = req_rows(4)
    ids = [r["hrs_reqid"] for r in rows]
    one = {"duplicates": [{"reqId": ids[1], "duplicateOf": ids[0], "reason": "same"}], "contradictions": [], "ambiguous": [], "outOfScope": []}
    two = {"duplicates": [], "contradictions": [], "ambiguous": [{"reqId": ids[3], "reason": "vague"}], "outOfScope": []}
    out = stages.relation_updates(str(uuid.uuid4()), rows, [one, two], [one, two], [one, two])
    assert len(out["duplicateUpdates"]) == 1 and [a["reqId"] for a in out["attributeUpdates"] if a["hrs_ambiguous"]] == [ids[3]]
    cands = [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]
    passes = [[{"requirementIds": ["c1"]}, {"requirementIds": ["c3"]}]] * 2 + [[{"requirementIds": []}]]
    assert [c["id"] for c in resolve_identified(cands, passes)["candidates"]] == ["c1", "c3"]
    drafted = [{"items": [{"key": "A-1", "questionText": "q1"}]}, {"items": [{"key": "A-2", "questionText": "q2"}]}]
    items = [{"key": k, "reqIds": [k], "trigger": "Ambiguity", "section": ""} for k in ("A-1", "A-2")]
    assert len(stages.resolve_clarifications(str(uuid.uuid4()), items, drafted)["rows"]) == 2


def test_code_stages_are_fast_at_ten_times_volume():
    """Shortlist and selection items for 500 requirements against 2,000 approved answers, well inside the
    Function's timeout (a flow's synchronous call waits 120 s)."""
    cats = ("SEC", "TECH", "IMPL", "SUPP", "AVAIL")
    reqs = req_rows(500, cats)
    approved = choice_value("hrs_approvalstatus", "Approved")
    answers = [{"hrs_answerid": str(uuid.uuid4()), "hrs_answercode": f"ANS-{i:04d}",
                "hrs_question": f"Do you provide capability number {i} with monitoring and reporting for {cats[i % 5]}?",
                "hrs_answertext": f"Yes. Capability {i} is provided.", "hrs_approvalstatus": approved, "hrs_lastreviewed": "2026-01-15",
                "hrs_confidentiality": choice_value("hrs_confidentiality", "Public"), "_hrs_supersededby_value": None,
                "hrs_categorycode": {"hrs_categorycode": cats[i % 5]}} for i in range(2000)]
    start = time.perf_counter()
    short = stages.shortlist_items(reqs, answers)
    sel = stages.selection_items(reqs, answers, [])
    elapsed = time.perf_counter() - start
    assert len(short["itemBatches"]) == 20 and sel["batches"]
    assert elapsed < 20, f"shortlist and selection items took {elapsed:.1f} s"  # 1.8 s on a developer machine


def test_relation_naming_something_else_is_dropped_with_a_warning():
    rows = req_rows(3)
    ids = [r["hrs_reqid"] for r in rows]
    bad = {"duplicates": [], "ambiguous": [], "outOfScope": [],
           "contradictions": [{"reqIds": [ids[0], "Product_Halcyon_AssetIQ.docx"], "reason": "x"}]}
    out = stages.relation_updates(str(uuid.uuid4()), rows, bad, bad, bad)
    assert out["problems"] == [] and out["warnings"] and out["flags"] == []


def test_duplicate_chains_resolve_to_a_root_and_loops_are_dropped():
    rows = req_rows(5)
    a, b, c, x, y = (r["hrs_reqid"] for r in rows)
    rel = {"contradictions": [], "ambiguous": [], "outOfScope": [],
           "duplicates": [{"reqId": a, "duplicateOf": b, "reason": "r"}, {"reqId": b, "duplicateOf": c, "reason": "r"},
                          {"reqId": x, "duplicateOf": y, "reason": "r"}, {"reqId": y, "duplicateOf": x, "reason": "r"}]}
    out = stages.relation_updates(str(uuid.uuid4()), rows, rel, rel, rel)
    by_id = {r["hrs_reqid"]: r["hrs_requirementid"] for r in rows}
    targets = {u["reqId"]: u["duplicateOf"] for u in out["duplicateUpdates"]}
    assert targets[a] == targets[b] == f"hrs_requirements({by_id[c]})"
    assert len([k for k in targets if k in (x, y)]) == 1 and out["warnings"]


def test_every_requirement_gets_compliance_even_with_chained_or_looped_duplicates():
    rows = req_rows(5)
    ids = [r["hrs_requirementid"] for r in rows]
    rows[0]["_hrs_duplicateof_value"] = ids[1]   # chain 0 -> 1 -> 2
    rows[1]["_hrs_duplicateof_value"] = ids[2]
    rows[3]["_hrs_duplicateof_value"] = ids[4]   # loop 3 <-> 4
    rows[4]["_hrs_duplicateof_value"] = ids[3]
    out = stages.resolve_policy(str(uuid.uuid4()), rows, [], [], [], [], {"items": []}, [], [])
    assert sorted(u["reqId"] for u in out["complianceUpdates"]) == sorted(r["hrs_reqid"] for r in rows)
