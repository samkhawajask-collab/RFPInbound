"""Deterministic operations for the RFP Response Factory.

Everything here is exact by construction: word counts, date comparisons, precedence tables and
lookups. No function in this package calls a model. See ARCHITECTURE.md section 3.5 and ADR-0006.
"""

from rfp_tools.approval import derive_approval_level, value_band_lower_bound
from rfp_tools.certifications import Certification, CertificationStatus, check_certifications
from rfp_tools.compliance import derive_compliance
from rfp_tools.model import ApprovalLevel, Compliance, FiredRule, RequirementStatus, Severity
from rfp_tools.routing import route_sme
from rfp_tools.words import CountingRule, count_words

__all__ = [
    "ApprovalLevel",
    "Certification",
    "CertificationStatus",
    "Compliance",
    "CountingRule",
    "FiredRule",
    "RequirementStatus",
    "Severity",
    "check_certifications",
    "count_words",
    "derive_approval_level",
    "derive_compliance",
    "route_sme",
    "value_band_lower_bound",
]
