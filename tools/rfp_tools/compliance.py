"""derive_compliance: the precedence table in ARCHITECTURE.md section 8.4.

    1. status == Withdrawn                      -> Withdrawn
    2. out of scope OR any Block rule fired     -> No
    3. ambiguous OR in a contradiction group    -> Clarify
    4. any Warn rule fired                      -> Partial
    5. no approved library answer               -> Gap
    6. otherwise                                -> Full

A fired Block rule or an out-of-scope finding already states the company's position (the rule's
approved position), and a clarification is asked before anything is answered, so neither needs a
library answer. Gap means only that nothing else applies and no approved answer exists.

Info rules and library traps (stale, conflicting, retired-match, draft-present) never change the
value, so they are not inputs.
"""

from collections.abc import Iterable

from rfp_tools.model import Compliance, RequirementStatus, Severity


def derive_compliance(
    *,
    status: RequirementStatus | str,
    has_approved_answer: bool,
    out_of_scope: bool,
    fired_severities: Iterable[Severity | str],
    ambiguous: bool,
    in_contradiction_group: bool,
) -> Compliance:
    severities = {Severity(s) for s in fired_severities}
    if RequirementStatus(status) is RequirementStatus.WITHDRAWN:
        return Compliance.WITHDRAWN
    if out_of_scope or Severity.BLOCK in severities:
        return Compliance.NO
    if ambiguous or in_contradiction_group:
        return Compliance.CLARIFY
    if Severity.WARN in severities:
        return Compliance.PARTIAL
    if not has_approved_answer:
        return Compliance.GAP
    return Compliance.FULL
