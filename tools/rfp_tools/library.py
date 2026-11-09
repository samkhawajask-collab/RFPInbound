"""Answer selection support: the filtered candidate set and its annotations (ARCHITECTURE.md 7.2, hard rule 5).

Selection is a filtered query followed by a rerank. Retired and Draft answers are excluded before
anything is ranked, so no model ever sees their text; they surface only as flags. Staleness is a
date comparison against the submission date. Conflict detection uses question similarity among
Approved answers in the same category as the topic proxy (assumption A6, measured in Slice 2).
"""

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import lru_cache

from rfp_tools.rules import normalize_text

STALE_MONTHS = 18
# SAME_TOPIC applies to topic_similarity() between two library questions. Measured on the reference
# library (2026-10-28): at 0.5 it finds the RPO, penetration-test and support-hours conflicts with no
# false positives among 198 approved answers; pairs phrased with no shared words (data return,
# implementation duration) need semantic judgment - assumption A6, scored in Slice 2.
SAME_TOPIC = 0.5
REQUIREMENT_MATCH = 0.45  # cosine between a requirement and a library question, close enough to flag
RELATED_WITH_FIGURE = 0.2  # lower bar when both state the same distinctive figure (shares_figure)
# Common abbreviations, expanded so "What RPO do you offer?" meets "recovery point objective".
_ABBREVIATIONS = {
    "rpo": "recovery point objective", "rto": "recovery time objective", "sla": "service level agreement",
    "sso": "single sign on", "mfa": "multi factor authentication", "pentest": "penetration test",
}
_FREQUENCY = re.compile(r"\b(daily|weekly|monthly|quarterly|annually|annual|yearly|semi-annual|biannual)\b")

_STOP = {
    "a", "an", "the", "of", "to", "for", "in", "on", "or", "and", "by", "with", "at", "as", "is", "are", "be",
    "do", "does", "you", "your", "we", "our", "shall", "will", "must", "should", "can", "respondent", "vendor",
    "solution", "system", "provide", "describe", "what", "how", "which", "please", "any", "all", "that", "this",
}


@dataclass(frozen=True)
class Answer:
    answer_id: str
    question: str
    text: str
    category: str
    status: str  # Approved | Draft | Retired
    last_reviewed: date | None
    confidentiality: str = "Public"
    superseded_by: str = ""  # for a Retired answer: the answer that replaced it (hrs_supersededby)


@dataclass(frozen=True)
class Candidate:
    answer_id: str
    question: str
    text: str
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Annotation:
    answer_id: str
    stale: bool
    conflicting_answer_ids: list[str] = field(default_factory=list)
    retired_match_ids: list[str] = field(default_factory=list)
    draft_present_ids: list[str] = field(default_factory=list)
    nda_only: bool = False

    @property
    def flags(self) -> list[str]:
        f = []
        if self.stale:
            f.append("LIBRARY-STALE")
        if self.conflicting_answer_ids:
            f.append("LIBRARY-CONFLICT")
        if self.retired_match_ids:
            f.append("LIBRARY-RETIRED-MATCH")
        if self.draft_present_ids:
            f.append("LIBRARY-DRAFT-PRESENT")
        return f

    @property
    def sme_review_required(self) -> bool:
        # NDA-only answers need SME review until an NDA status exists per opportunity (Q7).
        return self.stale or bool(self.conflicting_answer_ids) or self.nda_only

    def to_dict(self) -> dict:
        d = asdict(self)
        d["flags"] = self.flags
        d["sme_review_required"] = self.sme_review_required
        return d


def _stem(t: str) -> str:
    for suffix in ("ing", "ies", "es", "ed", "s"):
        if len(t) > 4 and t.endswith(suffix):
            return t[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return t


# Tokens and vectors are cached per text: one call compares every requirement with every library answer, so
# without the cache each answer is re-tokenized once per requirement (ADR-0029: 500 x 2,000 took 20 s, not 1).
@lru_cache(maxsize=65536)
def _tokens(text: str) -> tuple[str, ...]:
    text = re.sub(r"\(detailed response\)", "", text, flags=re.IGNORECASE)
    words = " ".join(_ABBREVIATIONS.get(w, w) for w in normalize_text(text).split()).split()
    return tuple(_stem(t) for t in words if t not in _STOP and len(t) > 1)


@lru_cache(maxsize=65536)
def _vector(text: str) -> Counter:
    """Shared between callers: read it, never change it."""
    return Counter(_tokens(text))


def _cosine(va: Counter, vb: Counter) -> float:
    if not va or not vb:
        return 0.0
    dot = sum(va[k] * vb[k] for k in va.keys() & vb.keys())
    return dot / (math.sqrt(sum(v * v for v in va.values())) * math.sqrt(sum(v * v for v in vb.values())))


def topic_similarity(question_a: str, question_b: str) -> float:
    """Blend of word and word-pair overlap, so "recovery point" and "recovery time" stay apart."""
    ta, tb = _tokens(question_a), _tokens(question_b)
    unigrams = _cosine(Counter(ta), Counter(tb))
    bigrams = _cosine(Counter(zip(ta, ta[1:])), Counter(zip(tb, tb[1:])))
    return 0.5 * unigrams + 0.5 * bigrams


def answer_facts(text: str) -> set[str]:
    """Numbers, clock times and frequency words: what two answers on one topic can disagree about."""
    lowered = text.lower()
    return set(re.findall(r"\d+(?::\d+)?(?:\.\d+)?", lowered)) | set(_FREQUENCY.findall(lowered))


def facts_conflict(text_a: str, text_b: str) -> bool:
    """A long-form answer that repeats and extends a short one does not conflict with it."""
    fa, fb = answer_facts(text_a), answer_facts(text_b)
    return bool(fa and fb and not (fa <= fb or fb <= fa))


def similarity(a: str, b: str) -> float:
    return _cosine(_vector(a), _vector(b))


def shares_figure(a: str, b: str) -> bool:
    """Both texts state the same distinctive figure (a decimal, a clock time or a number of 3+ digits),
    e.g. "99.99", and also share some wording: strong evidence they are about the same commitment."""
    distinctive = {f for f in answer_facts(a) & answer_facts(b) if any(ch in f for ch in ".:") or len(f) >= 3}
    words = lambda t: re.sub(r"\d+(?:[.:]\d+)?", " ", t)  # the shared wording must not count the figure itself
    return bool(distinctive) and similarity(words(a), words(b)) >= RELATED_WITH_FIGURE


def is_related(requirement_text: str, selected_question: str | None, candidate: "Answer", category: str) -> bool:
    """Whether a Retired or Draft answer is about what the requirement (or the selected answer) is about,
    compared in code on questions only. Word overlap counts only within `category` (across categories it is
    too noisy to tell apart from coincidence); a shared distinctive figure counts in any category. The
    meaning-based shortlist covers the rest (ADR-0019)."""
    q = candidate.question
    if candidate.category == category:
        if selected_question is not None and (normalize_text(q) == normalize_text(selected_question)
                                              or topic_similarity(q, selected_question) >= SAME_TOPIC):
            return True
        if similarity(requirement_text, q) >= REQUIREMENT_MATCH:
            return True
    return shares_figure(requirement_text, q)


def stale_cutoff(submission_date: date, months: int = STALE_MONTHS) -> date:
    y, m = divmod(submission_date.month - 1 - months, 12)
    year, month = submission_date.year + y, m + 1
    day = min(submission_date.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                                    31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def is_stale(answer: Answer, submission_date: date) -> bool:
    """Last reviewed more than 18 months before the submission date (A7). Never reviewed counts as stale."""
    return answer.last_reviewed is None or answer.last_reviewed < stale_cutoff(submission_date)


def select_answer_candidates(requirement_text: str, category: str, library: Iterable[Answer], top: int = 5) -> list[Candidate]:
    """Approved answers in the requirement's category, best match first. Retired and Draft never appear."""
    pool = [a for a in library if a.status == "Approved" and a.category == category]
    scored = [Candidate(a.answer_id, a.question, a.text, round(max(similarity(requirement_text, a.question),
                                                                    similarity(requirement_text, f"{a.question} {a.text}")), 4))
              for a in pool]
    scored.sort(key=lambda c: (-c.score, c.answer_id))
    return [c for c in scored[:top] if c.score > 0]


def annotate_answer(selected_id: str, requirement_text: str, library: Sequence[Answer], submission_date: date,
                    related_unusable: Iterable[str] = (), related_approved: Iterable[str] = ()) -> Annotation:
    """related_unusable: Retired or Draft IDs the shortlist prompt judged to be on the requirement's topic.
    related_approved: Approved IDs the shortlist judged to be on the same topic; one that states different facts
    from the selected answer is a conflict even when the two questions share no words (A6)."""
    by_id = {a.answer_id: a for a in library}
    selected = by_id[selected_id]
    if selected.status != "Approved":
        raise ValueError(f"{selected_id} is {selected.status}; only Approved answers may be selected (hard rule 5)")
    others = [a for a in library if a.answer_id != selected_id]
    # Conflicts: Approved answers in the same category on the same topic stating different facts (A6).
    same_topic = set(related_approved)
    conflicts = [a.answer_id for a in others if a.status == "Approved"
                 and ((a.category == selected.category and topic_similarity(a.question, selected.question) >= SAME_TOPIC)
                      or a.answer_id in same_topic)
                 and facts_conflict(a.text, selected.text)]
    # Retired and Draft matches: the superseded-by link, code similarity (is_related), or the shortlist's judgment.
    flagged = set(related_unusable)
    retired = [a.answer_id for a in others if a.status == "Retired" and (a.superseded_by == selected_id or a.answer_id in flagged
                                                                         or is_related(requirement_text, selected.question, a, selected.category))]
    drafts = [a.answer_id for a in others if a.status == "Draft"
              and (a.answer_id in flagged or is_related(requirement_text, selected.question, a, selected.category))]
    return Annotation(
        answer_id=selected_id,
        stale=is_stale(selected, submission_date),
        conflicting_answer_ids=sorted(conflicts),
        retired_match_ids=sorted(retired),
        draft_present_ids=sorted(drafts),
        nda_only=selected.confidentiality == "NDA-only",
    )


def related_unusable(requirement_text: str, category: str, library: Iterable[Answer], status: str,
                     judged: Iterable[str] = ()) -> list[str]:
    """Retired or Draft answers (per `status`) on the requirement's topic when no Approved answer was selected,
    so LIBRARY-RETIRED-MATCH / LIBRARY-DRAFT-PRESENT are still raised. `judged` are the shortlist's IDs.
    No model sees Retired or Draft answer text (hard rule 5)."""
    judged = set(judged)
    return sorted(a.answer_id for a in library if a.status == status
                  and (a.answer_id in judged or is_related(requirement_text, None, a, category)))


def map_response_code(compliance: str, buyer_codes: Sequence[tuple[str, str]]) -> list[str]:
    """Buyer response codes allowed for an internal compliance value, read from each code's meaning.

    buyer_codes: (code, meaning) pairs from the RFP, e.g. ("F", "Fully compliant ...").
    Gap, Clarify and Withdrawn have no drafted response, so they allow no code.
    """
    wanted = {
        "Full": r"\bfull|configur",
        "Partial": r"partial|roadmap|future|custom",
        "No": r"\bnot\b|\bnon-?compliant|cannot|does not",
    }.get(compliance)
    if wanted is None:
        return []
    if compliance != "No":
        return [c for c, meaning in buyer_codes if re.search(wanted, meaning, re.IGNORECASE)
                and not re.search(r"\bnot\b|non-?compliant", meaning, re.IGNORECASE)]
    return [c for c, meaning in buyer_codes if re.search(wanted, meaning, re.IGNORECASE)]
