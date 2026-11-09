"""route_sme: SME routing for a gap (ARCHITECTURE.md section 9.1).

    sme = category.primarySME
    if any Block or Warn rule fired:
        sme = escalationOwner(worst fired rule)      # Block before Warn
    # Info-severity rules never override the category SME

When several rules share the worst severity, the lowest rule ID wins (assumption; the documented
rule does not break ties, so this keeps routing stable across re-runs).
"""

import re
from collections.abc import Iterable, Mapping

from rfp_tools.model import FiredRule, Severity

_RANK = {Severity.BLOCK: 0, Severity.WARN: 1}


def _rule_sort_key(rule_id: str) -> tuple[str, int]:
    # R-02 before R-10: compare the numeric part as a number.
    m = re.fullmatch(r"(.*?)(\d+)", rule_id)
    return (m.group(1), int(m.group(2))) if m else (rule_id, 0)


def route_sme(category_code: str, fired_rules: Iterable[FiredRule], primary_sme_by_category: Mapping[str, str]) -> str:
    escalating = [r for r in fired_rules if Severity(r.severity) in _RANK]
    if escalating:
        worst = min(escalating, key=lambda r: (_RANK[Severity(r.severity)], _rule_sort_key(r.rule_id)))
        return worst.escalation_sme
    try:
        return primary_sme_by_category[category_code]
    except KeyError:
        raise ValueError(f"Unknown category code: {category_code!r}") from None
