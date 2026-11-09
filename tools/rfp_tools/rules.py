"""match_rule_candidates: recall-first phrase matching against the rule register (ARCHITECTURE.md 8.2).

Trigger phrases in the register are short paraphrases ("availability above 99.9%", "payment terms
longer than net 60"), not literal strings, so a phrase is a candidate when:

1. its normalized form appears in the normalized requirement text, or
2. every content word of the phrase appears in the text (comparatives, numbers and stop words are
   ignored, because the numeric part is judged by evaluate_thresholds), or
3. the rule has a numeric threshold whose metric the text mentions.

Precision comes later, from the adjudication prompt reading hrs_condition.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass

from rfp_tools.thresholds import Threshold, extract_metric_values

_STOP = {
    "a", "an", "the", "of", "to", "for", "in", "on", "or", "and", "by", "with", "at", "as", "is", "be",
    "than", "above", "below", "over", "under", "less", "more", "longer", "shorter", "within", "beyond",
    "without", "outside", "fixed", "required", "requires", "require",
}


def normalize_text(text: str) -> str:
    t = text.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[^a-z0-9.%$]+", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)  # keep decimal points only
    return re.sub(r"\s+", " ", t).strip()


def _stem(token: str) -> str:
    for suffix in ("ies", "es", "s"):
        if len(token) > 4 and token.endswith(suffix):
            return token[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return token


def _content_tokens(phrase: str) -> set[str]:
    return {_stem(t) for t in normalize_text(phrase).split() if t not in _STOP and not re.search(r"\d", t)}


@dataclass(frozen=True)
class RuleCandidate:
    rule_id: str
    reason: str  # "phrase", "words" or "threshold"
    matched: str  # the trigger phrase or metric that matched

    def to_dict(self) -> dict:
        return asdict(self)


def match_rule_candidates(
    text: str,
    trigger_patterns: Mapping[str, str],
    thresholds: Iterable[Threshold] = (),
) -> list[RuleCandidate]:
    """trigger_patterns: rule ID -> hrs_triggerpattern (semicolon-separated phrases)."""
    norm = f" {normalize_text(text)} "
    tokens = {_stem(t) for t in norm.split()}
    out: dict[str, RuleCandidate] = {}
    for rule_id in sorted(trigger_patterns):
        for phrase in (p.strip() for p in trigger_patterns[rule_id].split(";")):
            if not phrase:
                continue
            if f" {normalize_text(phrase)} " in norm:
                out[rule_id] = RuleCandidate(rule_id, "phrase", phrase)
                break
            words = _content_tokens(phrase)
            if words and words <= tokens:
                out.setdefault(rule_id, RuleCandidate(rule_id, "words", phrase))
    mentioned = extract_metric_values(text)
    for t in thresholds:
        if t.metric in mentioned and t.rule_id not in out:
            out[t.rule_id] = RuleCandidate(t.rule_id, "threshold", t.metric)
    return [out[k] for k in sorted(out)]
