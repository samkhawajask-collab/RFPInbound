"""evaluate_thresholds and match_rule_candidates on synthetic sentences.

Thresholds are loaded from data/supplement (the same data the pipeline seeds into Dataverse); trigger
phrases from the reference policy-rule CSV. No expected outcome comes from gold data.
"""

import csv

import pytest

from rfp_tools.rules import match_rule_candidates, normalize_text
from rfp_tools.thresholds import Threshold, evaluate_thresholds, extract_metric_values
from tests.conftest import REPO_ROOT, read_reference_csv


@pytest.fixture(scope="module")
def thresholds() -> list[Threshold]:
    with open(REPO_ROOT / "data" / "supplement" / "policy_rule_thresholds.csv", newline="", encoding="utf-8") as f:
        return [Threshold(r["hrs_ruleid"], r["hrs_thresholdmetric"], r["hrs_thresholdoperator"], float(r["hrs_thresholdvalue"]))
                for r in csv.DictReader(f)]


@pytest.fixture(scope="module")
def triggers() -> dict[str, str]:
    return {r["hrs_ruleid"]: r["hrs_triggerpattern"] for r in read_reference_csv("04_hrs_policyrule.csv")}


def breached(text, thresholds):
    return {r.rule_id for r in evaluate_thresholds(text, thresholds) if r.breached}


def test_every_threshold_rule_exists_in_the_register(thresholds, triggers):
    assert {t.rule_id for t in thresholds} <= set(triggers)
    assert len(thresholds) == 14


@pytest.mark.parametrize(
    ("text", "rule", "expect"),
    [
        ("The system shall be available 99.95% of the time each month.", "R-02", True),
        ("Availability shall be at least 99.9% per calendar month.", "R-02", False),
        ("Availability shall be 99.5% monthly.", "R-02", False),
        ("Invoices are payable net 90.", "R-04", True),
        ("Payment terms are net 30 from receipt of a correct invoice.", "R-04", False),
        ("Payment will be made within 75 days of invoice approval.", "R-04", True),
        ("Liquidated damages of 15% of annual contract value apply.", "R-05", True),
        ("Liquidated damages are capped at 5% of the annual fees.", "R-05", False),
        ("Vendor shall carry cyber liability insurance of $20 million.", "R-07", True),
        ("Cyber liability coverage of $5,000,000 is required.", "R-07", False),
        ("The RPO shall not exceed 15 minutes.", "R-12", True),
        ("Recovery point objective of 4 hours.", "R-12", False),
        ("The recovery time objective (RTO) shall be two (2) hours.", "R-13", True),
        ("RTO of 8 hours is acceptable.", "R-13", False),
        ("The vendor shall notify the Authority of any security breach within 24 hours.", "R-14", True),
        ("Breach notification within 72 hours of confirmation.", "R-14", False),
        ("Priority 1 incidents require a response within 30 minutes.", "R-21", True),
        ("Critical (P1) issues: response within 1 hour.", "R-21", False),
        ("The Authority may terminate for convenience on thirty (30) days written notice.", "R-23", True),
        ("Either party may terminate on 120 days notice.", "R-23", False),
        ("Pricing shall remain firm for 5 years.", "R-24", True),
        ("Prices are fixed for the 3 years of the initial term.", "R-24", False),
        ("The portal shall conform to WCAG 2.2 Level AAA.", "R-28", True),
        ("The portal shall conform to WCAG 2.1 AA.", "R-28", False),
        ("A performance bond of 25% of the contract value is required.", "R-30", True),
        ("A performance bond equal to 10% is required.", "R-30", False),
        ("Non-solicitation of staff applies for 24 months after the term.", "R-31", True),
        ("Go-live is required within 4 months of contract award.", "R-33", True),
        ("Go-live within 9 months of award.", "R-33", False),
    ],
)
def test_threshold_breach(text, rule, expect, thresholds):
    assert (rule in breached(text, thresholds)) is expect


def test_numbers_without_context_are_ignored(thresholds):
    assert evaluate_thresholds("The Authority serves 99.99% of residents.", thresholds) == []


def test_terminate_without_notice_is_zero_days(thresholds):
    assert extract_metric_values("The Authority may terminate the contract without notice.")["termination_notice_days"] == [0.0]
    assert "R-23" in breached("The Authority may terminate the contract without notice.", thresholds)


def test_unknown_metric_rejected():
    with pytest.raises(ValueError):
        evaluate_thresholds("x", [Threshold("R-99", "mystery", "gt", 1)])


def test_normalize_keeps_decimals_and_symbols():
    assert normalize_text("Uptime: 99.95%, $10M; WCAG-2.1.") == "uptime 99.95% $10m wcag 2.1"


def test_phrase_match(triggers):
    rules = {c.rule_id: c for c in match_rule_candidates("The Respondent shall accept unlimited liability.", triggers)}
    assert rules["R-01"].reason == "phrase"


def test_word_match_ignores_comparatives(triggers):
    rules = {c.rule_id for c in match_rule_candidates("Production availability must be 99.99%.", triggers)}
    assert "R-02" in rules


def test_threshold_metric_makes_a_candidate(triggers, thresholds):
    rules = {c.rule_id: c.reason for c in match_rule_candidates("Recovery point objective: 10 minutes.", triggers, thresholds)}
    assert rules.get("R-12") in ("threshold", "words", "phrase")


def test_unrelated_text_has_no_candidates(triggers, thresholds):
    assert match_rule_candidates("The vendor shall provide a user guide in PDF format.", triggers, thresholds) == []


def test_candidates_are_sorted_and_unique(triggers, thresholds):
    cands = match_rule_candidates("Hosting must be on-premises in the agency data center with unlimited liability.", triggers, thresholds)
    ids = [c.rule_id for c in cands]
    assert ids == sorted(set(ids))
    assert {"R-01", "R-09"} <= set(ids)
