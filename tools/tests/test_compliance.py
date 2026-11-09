"""Built from the documented precedence (section 8.4), not from gold data."""

import pytest

from rfp_tools import Compliance, Severity, derive_compliance

BASE = dict(
    status="Active",
    has_approved_answer=True,
    out_of_scope=False,
    fired_severities=[],
    ambiguous=False,
    in_contradiction_group=False,
)


def derive(**overrides):
    return derive_compliance(**{**BASE, **overrides})


def test_default_is_full():
    assert derive() is Compliance.FULL


def test_withdrawn_outranks_everything():
    assert derive(status="Withdrawn", has_approved_answer=False, fired_severities=["Block"]) is Compliance.WITHDRAWN


def test_block_outranks_gap():
    # A Block rule states the company's position without a library answer.
    assert derive(has_approved_answer=False, fired_severities=["Block"]) is Compliance.NO


def test_clarify_and_partial_outrank_gap():
    assert derive(has_approved_answer=False, ambiguous=True) is Compliance.CLARIFY
    assert derive(has_approved_answer=False, fired_severities=["Warn"]) is Compliance.PARTIAL


def test_gap_when_nothing_else_applies():
    assert derive(has_approved_answer=False, fired_severities=["Info"]) is Compliance.GAP


@pytest.mark.parametrize("overrides", [dict(out_of_scope=True), dict(fired_severities=["Block"])])
def test_no(overrides):
    assert derive(**overrides) is Compliance.NO


def test_block_outranks_clarify():
    # CV-039 pattern: in a contradiction group and fires a Block rule -> No, not Clarify.
    assert derive(in_contradiction_group=True, fired_severities=["Block"]) is Compliance.NO


def test_clarify_outranks_partial():
    # THPA-040 pattern: fires a Warn rule and sits in a contradiction group -> Clarify.
    assert derive(in_contradiction_group=True, fired_severities=["Warn"]) is Compliance.CLARIFY


def test_ambiguous_is_clarify():
    assert derive(ambiguous=True) is Compliance.CLARIFY


def test_warn_is_partial():
    assert derive(fired_severities=[Severity.WARN]) is Compliance.PARTIAL


def test_info_never_changes_compliance():
    # R-26 (HIPAA/BAA) and R-34 (subcontracting) are Info; their requirements stay Full.
    assert derive(fired_severities=["Info", "Info"]) is Compliance.FULL


def test_block_and_warn_together_is_no():
    assert derive(fired_severities=["Warn", "Block"]) is Compliance.NO


def test_rejects_unknown_severity():
    with pytest.raises(ValueError):
        derive(fired_severities=["Critical"])
