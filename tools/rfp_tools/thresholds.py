"""evaluate_thresholds: typed values from requirement text, compared arithmetically (ARCHITECTURE.md 8.2).

"99.95% > 99.9%" is arithmetic, not opinion. Each metric pairs a context pattern (what the number is
about) with a unit extractor (how to read the number). The thresholds themselves are data
(hrs_policyrule.hrs_threshold*, seeded from data/supplement/policy_rule_thresholds.csv) and are
passed in, never hard-coded here.
"""

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass

_NUM = r"(\d+(?:,\d{3})*(?:\.\d+)?)"
_PERCENT = re.compile(_NUM + r"\s*(?:%|percent\b)", re.IGNORECASE)
_MONEY = re.compile(r"\$\s*" + _NUM + r"\s*(million|mil|m|billion|bn|b|thousand|k)?\b", re.IGNORECASE)
_DURATION = re.compile(
    _NUM + r"\s*\)?\s*(?:-\s*)?(business\s+days?|calendar\s+days?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)
_NET = re.compile(r"\bnet\s*-?\s*" + _NUM + r"\b", re.IGNORECASE)
_WCAG = re.compile(r"\bWCAG\s*(\d(?:\.\d)?)\s*(?:level\s*)?(AAA|AA|A)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.;:])\s+(?=[A-Z(])|\n+")

_HOURS_PER = {"minute": 1 / 60, "min": 1 / 60, "hour": 1, "hr": 1, "day": 24, "business day": 24, "calendar day": 24,
              "week": 168, "month": 730, "year": 8760}
_MONEY_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "mil": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9}


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _unit_key(unit: str) -> str:
    u = re.sub(r"\s+", " ", unit.lower()).rstrip("s")
    return {"min": "minute", "hr": "hour"}.get(u, u)


def _durations(sentence: str, target: str) -> list[float]:
    """Durations converted to `target` unit (hours | days | months | years)."""
    out = []
    for m in _DURATION.finditer(sentence):
        hours = _num(m.group(1)) * _HOURS_PER[_unit_key(m.group(2))]
        out.append({"hours": hours, "days": hours / 24, "months": hours / 730, "years": hours / 8760}[target])
    return out


def _percents(sentence: str) -> list[float]:
    return [_num(m.group(1)) for m in _PERCENT.finditer(sentence)]


def _money(sentence: str) -> list[float]:
    out = []
    for m in _MONEY.finditer(sentence):
        scale = _MONEY_SCALE.get((m.group(2) or "").lower(), 1)
        out.append(_num(m.group(1)) * scale)
    return out


def _net_days(sentence: str) -> list[float]:
    vals = [_num(m.group(1)) for m in _NET.finditer(sentence)]
    if not vals and re.search(r"invoice|payment", sentence, re.IGNORECASE):
        vals = _durations(sentence, "days")
    return vals


def _wcag(sentence: str) -> list[float]:
    levels = {"A": 1, "AA": 2, "AAA": 3}
    return [round(float(m.group(1)) + levels[m.group(2).upper()] / 100, 2) for m in _WCAG.finditer(sentence)]


# metric -> (context pattern that must appear in the sentence, extractor)
METRICS = {
    "availability_percent": (r"availab|uptime|up-time|service level", _percents),
    "payment_net_days": (r"\bnet\b|payment|invoice", _net_days),
    "penalty_percent": (r"liquidated damages|penalt", _percents),
    "cyber_insurance_usd": (r"cyber", _money),
    "rpo_hours": (r"\bRPO\b|recovery point", lambda s: _durations(s, "hours")),
    "rto_hours": (r"\bRTO\b|recovery time", lambda s: _durations(s, "hours")),
    "breach_notification_hours": (r"(?=.*notif)(?=.*(breach|incident))", lambda s: _durations(s, "hours")),
    "p1_response_hours": (r"(?=.*(priority\s*1|\bP1\b|severity\s*1|critical))(?=.*respon)", lambda s: _durations(s, "hours")),
    "termination_notice_days": (r"(?=.*terminat)(?=.*notice)", lambda s: _durations(s, "days")),
    "price_hold_years": (r"(?=.*(price|pricing|rates))(?=.*(hold|firm|fixed|lock))", lambda s: _durations(s, "years")),
    "wcag_level": (r"WCAG", _wcag),
    "performance_bond_percent": (r"performance bond", _percents),
    "non_solicitation_months": (r"non-?solicit", lambda s: _durations(s, "months")),
    "go_live_months": (r"go-?live|go live|in production|operational", lambda s: _durations(s, "months")),
}


def extract_metric_values(text: str) -> dict[str, list[float]]:
    """Every metric the text mentions, with the values found in the same sentence."""
    found: dict[str, list[float]] = {}
    for sentence in _SENTENCE.split(text):
        for metric, (context, extractor) in METRICS.items():
            if re.search(context, sentence, re.IGNORECASE | re.DOTALL):
                values = extractor(sentence)
                if values:
                    found.setdefault(metric, []).extend(values)
    # Termination "without notice" is a zero-day notice period.
    if re.search(r"terminat\w*[^.]*without\s+(?:prior\s+)?notice", text, re.IGNORECASE):
        found.setdefault("termination_notice_days", []).append(0.0)
    return found


@dataclass(frozen=True)
class Threshold:
    rule_id: str
    metric: str
    operator: str  # "gt" | "lt"
    value: float


@dataclass(frozen=True)
class ThresholdResult:
    rule_id: str
    metric: str
    operator: str
    threshold: float
    extracted_values: tuple[float, ...]
    breached: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        d["extracted_values"] = list(self.extracted_values)
        return d


def evaluate_thresholds(text: str, thresholds: Iterable[Threshold]) -> list[ThresholdResult]:
    """Results only for thresholds whose metric the text actually mentions."""
    values = extract_metric_values(text)
    results = []
    for t in sorted(thresholds, key=lambda t: t.rule_id):
        if t.metric not in METRICS:
            raise ValueError(f"Unknown threshold metric {t.metric!r} on {t.rule_id}")
        found = values.get(t.metric)
        if not found:
            continue
        if t.operator == "gt":
            breached = any(v > t.value for v in found)
        elif t.operator == "lt":
            breached = any(v < t.value for v in found)
        else:
            raise ValueError(f"Unknown operator {t.operator!r} on {t.rule_id}")
        results.append(ThresholdResult(t.rule_id, t.metric, t.operator, t.value, tuple(found), breached))
    return results
