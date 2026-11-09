"""Section 9.1, checked against the reference category and policy-rule registers."""

import pytest

from rfp_tools import FiredRule, Severity, route_sme


@pytest.fixture(scope="module")
def primary_sme(categories):
    return {c["hrs_categorycode"]: c["hrs_primarysmeid"] for c in categories}


def fired(policy_rules, *rule_ids):
    return [
        FiredRule(rid, Severity(policy_rules[rid]["hrs_severity"]), policy_rules[rid]["hrs_escalationsmeid"])
        for rid in rule_ids
    ]


def _first_rule(policy_rules, severity):
    return next(r for r in policy_rules.values() if r["hrs_severity"] == severity)


def test_no_rules_routes_to_category_primary(primary_sme):
    assert route_sme("COMP", [], primary_sme) == "SME-01"


def test_every_category_has_a_primary_sme(categories, primary_sme):
    for c in categories:
        assert route_sme(c["hrs_categorycode"], [], primary_sme) == c["hrs_primarysmeid"]


def test_info_rule_never_overrides(policy_rules, primary_sme):
    info = _first_rule(policy_rules, "Info")
    assert route_sme("COMP", fired(policy_rules, info["hrs_ruleid"]), primary_sme) == "SME-01"


def test_warn_routes_to_escalation_owner(policy_rules, primary_sme):
    warn = _first_rule(policy_rules, "Warn")
    assert route_sme("COMP", fired(policy_rules, warn["hrs_ruleid"]), primary_sme) == warn["hrs_escalationsmeid"]


def test_block_before_warn(policy_rules, primary_sme):
    warn = _first_rule(policy_rules, "Warn")
    block = _first_rule(policy_rules, "Block")
    rules = fired(policy_rules, warn["hrs_ruleid"], block["hrs_ruleid"])
    assert route_sme("COMP", rules, primary_sme) == block["hrs_escalationsmeid"]


def test_tie_breaks_on_lowest_rule_id_numerically():
    rules = [FiredRule("R-10", Severity.BLOCK, "SME-10"), FiredRule("R-02", Severity.BLOCK, "SME-02")]
    assert route_sme("COMP", rules, {"COMP": "SME-01"}) == "SME-02"


def test_unknown_category_raises():
    with pytest.raises(ValueError):
        route_sme("NOPE", [], {"COMP": "SME-01"})
