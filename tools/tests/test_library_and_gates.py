"""Answer selection/annotation and gates, on synthetic libraries plus the reference answer library.

Assertions follow the documented rules (ARCHITECTURE.md 7.2, 4.2) and the library's own data
(statuses, review dates, superseded-by links), never gold expectations.
"""

import io
from datetime import date

import pytest
from pypdf import PdfWriter

from rfp_tools.gates import count_pages, gate_g1, gate_g2, gate_g4, gate_g5, gate_g6
from rfp_tools.library import (
    Answer,
    annotate_answer,
    is_stale,
    map_response_code,
    select_answer_candidates,
    stale_cutoff,
)
from tests.conftest import read_reference_csv

SUBMIT = date(2026, 11, 20)


def lib():
    return [
        Answer("A1", "What is your RPO?", "Our RPO is 1 hour.", "AVAIL", "Approved", date(2026, 1, 10)),
        Answer("A2", "What is your recovery point objective (RPO)?", "Our RPO is 4 hours.", "AVAIL", "Approved", date(2026, 3, 1)),
        Answer("A3", "What is your RPO?", "Our RPO is 24 hours.", "AVAIL", "Retired", date(2022, 1, 1)),
        Answer("A4", "What is your RPO?", "We guarantee zero data loss.", "AVAIL", "Draft", date(2026, 9, 1)),
        Answer("A5", "Describe your uptime SLA.", "99.9% monthly.", "AVAIL", "Approved", date(2024, 1, 5)),
        Answer("A6", "Describe your encryption at rest.", "AES-256.", "SEC", "Approved", date(2026, 6, 1), "NDA-only"),
    ]


def test_candidates_are_approved_and_in_category_only():
    cands = select_answer_candidates("The vendor shall state its RPO.", "AVAIL", lib())
    ids = [c.answer_id for c in cands]
    assert "A3" not in ids and "A4" not in ids and "A6" not in ids
    assert ids[0] in ("A1", "A2")


def test_no_candidates_outside_category():
    assert select_answer_candidates("The vendor shall state its RPO.", "COMM", lib()) == []


def test_stale_cutoff_is_18_months_before_submission():
    assert stale_cutoff(SUBMIT) == date(2025, 5, 20)
    assert stale_cutoff(date(2026, 8, 31)) == date(2025, 2, 28)


def test_stale_and_conflict_and_traps_flagged():
    a = annotate_answer("A1", "The vendor shall state its RPO.", lib(), SUBMIT)
    assert not a.stale
    assert a.conflicting_answer_ids == ["A2"]
    assert a.retired_match_ids == ["A3"]
    assert a.draft_present_ids == ["A4"]
    assert a.flags == ["LIBRARY-CONFLICT", "LIBRARY-RETIRED-MATCH", "LIBRARY-DRAFT-PRESENT"]
    assert a.sme_review_required


def test_stale_answer_requires_sme_review():
    a = annotate_answer("A5", "State your availability SLA.", lib(), SUBMIT)
    assert a.stale and "LIBRARY-STALE" in a.flags and a.sme_review_required


def test_nda_only_requires_sme_review():
    assert annotate_answer("A6", "Encrypt data at rest.", lib(), SUBMIT).sme_review_required


def test_selecting_a_non_approved_answer_is_refused():
    with pytest.raises(ValueError):
        annotate_answer("A3", "x", lib(), SUBMIT)


def test_reference_library_retired_never_offered():
    rows = read_reference_csv("03_hrs_answer.csv")
    library = [Answer(r["hrs_answerid"], r["hrs_question"], r["hrs_answertext"], r["hrs_categorycode"],
                      r["hrs_approvalstatus"], date.fromisoformat(r["hrs_lastreviewed"]) if r["hrs_lastreviewed"] else None,
                      r["hrs_confidentiality"]) for r in rows]
    status = {a.answer_id: a.status for a in library}
    for r in rows:
        if r["hrs_approvalstatus"] != "Approved":
            cands = select_answer_candidates(r["hrs_question"], r["hrs_categorycode"], library, top=10)
            assert all(status[c.answer_id] == "Approved" for c in cands)
    # Every retired answer with a superseded-by link is flagged when its replacement is selected for its question.
    for r in rows:
        if r["hrs_approvalstatus"] == "Retired" and r["hrs_supersededby"]:
            ann = annotate_answer(r["hrs_supersededby"], r["hrs_question"], library, SUBMIT)
            assert r["hrs_answerid"] in ann.retired_match_ids


def test_detailed_response_variant_is_not_a_conflict():
    short = Answer("S1", "What support hours are provided?", "Support is 8:00 to 20:00, 24/7 for P1.", "SUPP", "Approved", SUBMIT)
    long = Answer("S2", "What support hours are provided? (detailed response)",
                  "Support is 8:00 to 20:00, 24/7 for P1, with quarterly reviews.", "SUPP", "Approved", SUBMIT)
    other = Answer("S3", "What are your standard support hours?", "Support is 8:00 to 18:00, 24/7 for P1.", "SUPP", "Approved", SUBMIT)
    ann = annotate_answer("S1", "State your support hours.", [short, long, other], SUBMIT)
    assert ann.conflicting_answer_ids == ["S3"]


def test_rpo_and_rto_are_different_topics():
    rpo = Answer("R1", "What is your recovery point objective?", "1 hour.", "AVAIL", "Approved", SUBMIT)
    rto = Answer("R2", "What is your recovery time objective?", "4 hours.", "AVAIL", "Approved", SUBMIT)
    assert annotate_answer("R1", "State your RPO.", [rpo, rto], SUBMIT).conflicting_answer_ids == []


def test_reference_library_stale_count_matches_its_dates():
    rows = read_reference_csv("03_hrs_answer.csv")
    approved = [Answer(r["hrs_answerid"], r["hrs_question"], r["hrs_answertext"], r["hrs_categorycode"], "Approved",
                       date.fromisoformat(r["hrs_lastreviewed"])) for r in rows if r["hrs_approvalstatus"] == "Approved"]
    expected = sum(1 for a in approved if a.last_reviewed < date(2025, 5, 20))
    assert sum(is_stale(a, SUBMIT) for a in approved) == expected


CODES = [("F", "Fully compliant as delivered"), ("C", "Compliant through configuration"),
         ("P", "Partially compliant"), ("R", "On the product roadmap (future release)"), ("N", "Not compliant")]


@pytest.mark.parametrize(("compliance", "expected"),
                         [("Full", ["F", "C"]), ("Partial", ["P", "R"]), ("No", ["N"]), ("Gap", []), ("Clarify", [])])
def test_map_response_code(compliance, expected):
    assert map_response_code(compliance, CODES) == expected


def _pdf(pages: int) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(612, 792)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_count_pages():
    assert count_pages(_pdf(3)) == 3


def test_gates():
    assert gate_g1(10, 10).passed and not gate_g1(9, 10).passed
    reqs = [{"req_id": "X-1", "location": "S1"}, {"req_id": "X-2", "location": "S2"}]
    assert gate_g2(reqs, 1, 5).passed
    assert not gate_g2(reqs + [{"req_id": "X-1", "location": ""}], 1, 5).passed
    assert not gate_g2(reqs, 3, 5).passed
    assert gate_g4([{"req_id": "X-1", "compliance": "Full"}, {"req_id": "X-2", "status": "Withdrawn"}]).passed
    assert not gate_g4([{"req_id": "X-1"}]).passed
    flags = [{"flag_type": "POLICY-RULE", "severity": "Block", "rule_id": "R-01", "req_id": "X-1"},
             {"flag_type": "POLICY-RULE", "severity": "Info", "rule_id": "R-26", "req_id": "X-2"},
             {"flag_type": "LIBRARY-STALE", "severity": "Info", "rule_id": "", "req_id": "X-2"}]
    assert gate_g5(flags, [("R-01", "X-1")]).passed
    assert not gate_g5(flags, []).passed
    assert gate_g6(28, 30, {"X-1": 200}, 250, ["Form 1"], ["Form 1", "Form 2"]).passed
    g6 = gate_g6(31, 30, {"X-1": 260}, 250, ["Form 1", "Form 3"], ["Form 1"])
    assert not g6.passed and len(g6.problems) == 3


def test_draft_in_another_category_needs_the_shortlist_judgment():
    """Across categories word overlap is too noisy, so a draft there is flagged only when the shortlist judged it related."""
    from rfp_tools.library import Answer, annotate_answer
    lib = [Answer("A-1", "Does the product support offline field work?", "Yes.", "MOB", "Approved", date(2026, 5, 1)),
           Answer("D-1", "Does the product support offline field work on every device?", "Draft.", "WORK", "Draft", date(2026, 9, 1)),
           Answer("R-1", "Which handheld platforms are supported?", "Old.", "WORK", "Retired", date(2020, 1, 1))]
    req = "The product shall support offline field work on handheld devices."
    assert annotate_answer("A-1", req, lib, date(2026, 11, 20)).draft_present_ids == []
    ann = annotate_answer("A-1", req, lib, date(2026, 11, 20), related_unusable=["D-1"])
    assert ann.draft_present_ids == ["D-1"] and ann.retired_match_ids == []


def test_shared_distinctive_figure_links_a_draft():
    from rfp_tools.library import shares_figure
    assert shares_figure("Guarantee 99.95% availability every month.", "Do you guarantee 99.95% availability?")
    assert not shares_figure("Provide 10 trainers.", "Do you provide 10 licenses?")          # small numbers are not distinctive
    assert not shares_figure("Respond within 99.95 seconds.", "Quarterly 99.95 audit cycle.")  # figure alone, no shared wording


def test_shortlist_same_topic_finds_conflicts_phrased_with_no_shared_words():
    a = Answer("T1", "What is the onboarding period?", "Onboarding takes 2 to 3 weeks.", "IMPL", "Approved", SUBMIT)
    b = Answer("T2", "How quickly can new users start?", "New users are productive within 5 weeks.", "IMPL", "Approved", SUBMIT)
    same = Answer("T3", "When can new users start? (detailed response)", "Onboarding takes 2 to 3 weeks, with a checklist.", "IMPL", "Approved", SUBMIT)
    assert annotate_answer("T1", "State the onboarding period.", [a, b, same], SUBMIT).conflicting_answer_ids == []
    ann = annotate_answer("T1", "State the onboarding period.", [a, b, same], SUBMIT, related_approved=["T2", "T3"])
    assert ann.conflicting_answer_ids == ["T2"]  # T3 repeats T1's facts, so it does not conflict
