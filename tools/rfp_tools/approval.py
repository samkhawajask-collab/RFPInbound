"""derive_approval_level: ARCHITECTURE.md section 9.3.

    levelFromValue = L1 if band <  $250k
                     L2 if band in [$250k, $1M]
                     L3 if band >  $1M
                     L4 if band >  $3M

    levelFromRisk  = L1 if no Warn and no Block flags
                     L2 if any Warn flag
                     L3 if any Block flag or legal exception
                     L4 if a Block flag is to be accepted as an exception

    approvalLevel  = max(levelFromValue, levelFromRisk)

Value bands arrive as text ("< $250,000", "$250,000 - $1,000,000", "$1,000,000 - $3,000,000",
"> $3,000,000"). A band is placed by its lower bound (assumption: the opportunity's band, not a
point value, is what the approver matrix keys on).
"""

import re
from collections.abc import Iterable

from rfp_tools.model import ApprovalLevel, Severity

_AMOUNT = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")


def value_band_lower_bound(value_band: str) -> float:
    band = value_band.strip()
    amounts = [float(a.replace(",", "")) for a in _AMOUNT.findall(band)]
    if not amounts:
        raise ValueError(f"Unrecognized value band: {value_band!r}")
    if band.startswith("<"):
        return 0.0
    return amounts[0]


def _level_from_value(value_band: str) -> ApprovalLevel:
    lower = value_band_lower_bound(value_band)
    if lower >= 3_000_000:
        return ApprovalLevel.L4
    if lower >= 1_000_000:
        return ApprovalLevel.L3
    if lower >= 250_000:
        return ApprovalLevel.L2
    return ApprovalLevel.L1


def _level_from_risk(severities: set[Severity], legal_exception: bool, block_accepted_as_exception: bool) -> ApprovalLevel:
    if block_accepted_as_exception:
        return ApprovalLevel.L4
    if Severity.BLOCK in severities or legal_exception:
        return ApprovalLevel.L3
    if Severity.WARN in severities:
        return ApprovalLevel.L2
    return ApprovalLevel.L1


def derive_approval_level(
    value_band: str,
    flag_severities: Iterable[Severity | str],
    *,
    legal_exception: bool = False,
    block_accepted_as_exception: bool = False,
) -> ApprovalLevel:
    severities = {Severity(s) for s in flag_severities}
    by_value = _level_from_value(value_band)
    by_risk = _level_from_risk(severities, legal_exception, block_accepted_as_exception)
    return max(by_value, by_risk, key=lambda level: level.rank)
