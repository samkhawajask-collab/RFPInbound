"""Addendum reconciliation (ADR-0010 amendment 1): majority, validation against the addendum text, effects.

The addendum fixture is a trimmed Layout response for RFP D's inbound addendum. The interpretations below are
what RFP Interpret Addendum is asked to produce from that text, written by hand here; none comes from gold data.
"""

import json

import pytest

from rfp_tools import addendum
from rfp_tools.pdf_layout import normalize_layout
from rfp_tools.schema import choice_label, choice_value
from tests.conftest import REPO_ROOT

RFP_ID = "00000000-0000-0000-0000-0000000000aa"


@pytest.fixture(scope="module")
def blocks():
    raw = json.loads((REPO_ROOT / "tools" / "tests" / "fixtures" / "rfp_d_addendum_layout.json").read_text(encoding="utf-8"))
    return [b.to_dict() for b in normalize_layout(raw)]


@pytest.fixture(scope="module")
def prompt(blocks):
    return addendum.prompt_input(blocks)


GOOD = {"changes": [
    {"item": "B.1", "kind": "modify", "reqId": "THPA-036", "newText": "The system must be available 99.95% of the time, measured monthly.",
     "mandatory": "Mandatory", "section": ""},
    {"item": "B.2", "kind": "withdraw", "reqId": "THPA-010", "newText": "", "mandatory": "", "section": ""},
    {"item": "B.3", "kind": "promote", "reqId": "THPA-020", "newText": "", "mandatory": "Mandatory", "section": ""},
    {"item": "B.4", "kind": "add", "reqId": "THPA-051",
     "newText": "The Vendor must perform a business process reengineering assessment prior to configuration.",
     "mandatory": "Mandatory", "section": "5.10"}],
    "dueDate": "2026-12-08", "pageLimit": 20, "acknowledgementRequired": True}
KNOWN = [f"THPA-{n:03d}" for n in range(1, 51)]


def test_prompt_input_marks_every_item(prompt):
    assert prompt["labels"] == ["A.1", "A.2", "B.1", "B.2", "B.3", "B.4"]
    assert "[B.4] B. 4 New requirement THPA-051" in prompt["text"]


def test_verified_changes_are_kept(prompt):
    v = addendum.validate(GOOD, prompt["text"], KNOWN, prompt["labels"])
    assert [c["reqId"] for c in v["changes"]] == ["THPA-036", "THPA-010", "THPA-020", "THPA-051"] and not v["rejected"]
    assert v["rfp"] == {"dueDate": "2026-12-08", "pageLimit": 20, "acknowledgementRequired": True}


@pytest.mark.parametrize("bad, reason", [
    ({"item": "B.1", "kind": "modify", "reqId": "THPA-036", "newText": "The system must be available 99.9% of the time, measured monthly.",
      "mandatory": "", "section": ""}, "verbatim"),
    ({"item": "B.2", "kind": "withdraw", "reqId": "THPA-099", "newText": "", "mandatory": "", "section": ""}, "not a requirement"),
    ({"item": "B.4", "kind": "add", "reqId": "THPA-012", "newText": "x", "mandatory": "", "section": ""}, "already exists"),
    ({"item": "Z.9", "kind": "withdraw", "reqId": "THPA-010", "newText": "", "mandatory": "", "section": ""}, "not an item"),
    ({"item": "B.3", "kind": "promote", "reqId": "THPA-020", "newText": "", "mandatory": "", "section": ""}, "Mandatory/Desirable"),
])
def test_unverifiable_changes_are_rejected_not_applied(prompt, bad, reason):
    v = addendum.validate({"changes": [bad]}, prompt["text"], KNOWN, prompt["labels"])
    assert not v["changes"] and reason in v["rejected"][0]["reason"]


def test_item_labels_written_with_brackets_or_a_prefix_are_normalized(prompt):
    written = {"changes": [{**c, "item": f"[{c['item']}]"} for c in GOOD["changes"][:2]]
               + [{**GOOD["changes"][2], "item": "Item B. 3"}]}
    v = addendum.validate(written, prompt["text"], KNOWN, prompt["labels"])
    assert [c["item"] for c in v["changes"]] == ["B.1", "B.2", "B.3"] and not v["rejected"]


def test_rfp_values_must_appear_in_the_addendum(prompt):
    v = addendum.validate({"changes": [], "dueDate": "2026-12-09", "pageLimit": 15}, prompt["text"], KNOWN, prompt["labels"])
    assert "dueDate" not in v["rfp"] and "pageLimit" not in v["rfp"] and len(v["rejected"]) == 2


def test_majority_of_passes():
    minority = {"changes": [{"item": "B.2", "kind": "modify", "reqId": "THPA-010", "newText": "x", "mandatory": "", "section": ""}],
                "dueDate": "2026-12-09", "pageLimit": 20, "acknowledgementRequired": True}
    got = addendum.agree([GOOD, GOOD, minority])
    assert [c["reqId"] for c in got["changes"]] == ["THPA-036", "THPA-010", "THPA-020", "THPA-051"]
    assert got["dueDate"] == "2026-12-08" and got["pageLimit"] == 20


def test_effects_follow_the_adr_0010_table(blocks, prompt):
    reqs = [{"hrs_reqid": rid, "hrs_requirementid": f"00000000-0000-0000-0000-{n:012d}"} for n, rid in enumerate(KNOWN, start=1)]
    v = addendum.validate(GOOD, prompt["text"], KNOWN, prompt["labels"])
    out = addendum.apply(RFP_ID, 1, reqs, v, addendum.item_locations(blocks, 1), ["Signature page"])
    fields = {u["reqId"]: u["fields"] for u in out["updates"]}
    assert fields["THPA-036"]["hrs_requirementtext"].endswith("99.95% of the time, measured monthly.")
    assert choice_label("hrs_requirementstatus", fields["THPA-010"]["hrs_status"]) == "Withdrawn"
    assert choice_label("hrs_mandatory", fields["THPA-020"]["hrs_mandatory"]) == "Mandatory"
    assert out["added"] == [{"req_id": "THPA-051", "text": "The Vendor must perform a business process reengineering assessment prior to configuration.",
                             "mandatory": "Mandatory", "section": "5.10", "location": "Addendum No. 1, item B.4 (page 1)", "introducedInRevision": 1}]
    types = sorted(choice_label("hrs_flagtype", f["hrs_flagtype"]) for f in out["flags"])
    assert types == ["ADDENDUM-CHANGE", "ADDENDUM-CHANGE", "ADDENDUM-WITHDRAWN"]
    assert out["rfp"]["hrs_duedate"] == "2026-12-08" and out["rfp"]["hrs_pagelimit"] == 20 and out["rfp"]["hrs_currentrevision"] == 1
    assert json.loads(out["rfp"]["hrs_requiredforms"]) == ["Signature page", "Acknowledgement of Addendum No. 1"]


def test_added_requirements_get_their_flag_once_written(blocks, prompt):
    v = addendum.validate(GOOD, prompt["text"], KNOWN, prompt["labels"])
    reqs = [{"hrs_reqid": rid, "hrs_requirementid": f"00000000-0000-0000-0000-{n:012d}"} for n, rid in enumerate(KNOWN, start=1)]
    out = addendum.apply(RFP_ID, 1, reqs, v, addendum.item_locations(blocks, 1), [])
    written = [{"hrs_reqid": "THPA-051", "hrs_requirementid": "00000000-0000-0000-0000-000000000051"}]
    rows = addendum.added_flag_rows(RFP_ID, 1, written, out["added"])
    assert rows["count"] == 1 and choice_label("hrs_flagtype", rows["rows"][0]["hrs_flagtype"]) == "ADDENDUM-CHANGE"
    assert rows["rows"][0]["hrs_detail"].startswith("Addendum No. 1, item B.4 (page 1)")
    assert "000000000051" in rows["rows"][0]["hrs_requirement@odata.bind"]


def test_rejected_items_become_review_flags(blocks, prompt):
    v = addendum.validate({"changes": [{"item": "B.2", "kind": "withdraw", "reqId": "THPA-099", "newText": "", "mandatory": "", "section": ""}]},
                          prompt["text"], KNOWN, prompt["labels"])
    out = addendum.apply(RFP_ID, 1, [], v, {}, [])
    assert not out["updates"] and "needs review" in out["flags"][0]["hrs_detail"]
    assert out["flags"][0]["hrs_flagtype"] == choice_value("hrs_flagtype", "ADDENDUM-CHANGE")


def test_service_addendum_items_and_apply(monkeypatch, blocks):
    import sys
    sys.path.insert(0, str(REPO_ROOT / "tools" / "service"))
    from tests.test_service import call
    status, items = call("addendum-items", {"blocks": blocks, "revision": 1})
    assert status == 200 and items["labels"][-1] == "B.4" and items["locations"]["B.4"] == "Addendum No. 1, item B.4 (page 1)"
    reqs = [{"hrs_reqid": rid, "hrs_requirementid": f"00000000-0000-0000-0000-{n:012d}"} for n, rid in enumerate(KNOWN, start=1)]
    status, out = call("apply-addendum", {"rfpId": RFP_ID, "revision": 1, "requirements": reqs, "passes": [GOOD, GOOD, GOOD],
                                          "addendumText": items["text"], "labels": items["labels"], "locations": items["locations"],
                                          "labelBlocks": items["labelBlocks"], "requiredForms": "[]"})
    assert status == 200 and out["addedCount"] == 1 and not out["rejected"]
    assert out["added"][0]["source_block_index"] == items["labelBlocks"]["B.4"]
    assert json.loads(out["addedJson"])[0]["reqId"] == "THPA-051"
    assert out["rfp"]["hrs_pagelimit"] == 20 and len(out["updates"]) == 3
    assert [u["reqId"] for u in out["textUpdates"]] == ["THPA-036"] and [u["reqId"] for u in out["statusUpdates"]] == ["THPA-010"]
    assert [u["reqId"] for u in out["mandatoryUpdates"]] == ["THPA-020"] and len(out["requirementFlags"]) == 3 and not out["reviewFlags"]

