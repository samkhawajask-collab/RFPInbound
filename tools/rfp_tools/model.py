"""Shared value types. Enum values match the Dataverse choice labels (ARCHITECTURE.md section 5.5)."""

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    BLOCK = "Block"
    WARN = "Warn"
    INFO = "Info"


class Compliance(str, Enum):
    FULL = "Full"
    PARTIAL = "Partial"
    NO = "No"
    CLARIFY = "Clarify"
    GAP = "Gap"
    WITHDRAWN = "Withdrawn"


class RequirementStatus(str, Enum):
    ACTIVE = "Active"
    WITHDRAWN = "Withdrawn"


class ApprovalLevel(str, Enum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"

    @property
    def rank(self) -> int:
        return int(self.value[1:])


@dataclass(frozen=True)
class FiredRule:
    """A policy rule that fired for a requirement, after adjudication."""

    rule_id: str
    severity: Severity
    escalation_sme: str
