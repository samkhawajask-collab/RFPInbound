"""Stage gates G1-G6 (ARCHITECTURE.md 4.2) and count_pages. Gates are code: a stage may not start until its gate passes."""

import io
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field

from pypdf import PdfReader


@dataclass
class GateResult:
    gate: str
    passed: bool
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _result(gate: str, problems: list[str]) -> GateResult:
    return GateResult(gate, not problems, problems)


def count_pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def gate_g1(block_count: int, source_units: int, unit: str = "body element") -> GateResult:
    """Every page (PDF) or body element (Word) of the source is represented by at least one block."""
    problems = [] if block_count >= source_units > 0 else [f"{block_count} blocks for {source_units} {unit}s"]
    return _result("G1", problems)


def gate_g2(requirements: Sequence[Mapping], expected_min: int, expected_max: int) -> GateResult:
    """Count within the format's expected band, no duplicate IDs, every requirement located."""
    problems = []
    n = len(requirements)
    if not expected_min <= n <= expected_max:
        problems.append(f"{n} requirements outside expected band {expected_min}-{expected_max}")
    dupes = [i for i, c in Counter(r["req_id"] for r in requirements).items() if c > 1]
    if dupes:
        problems.append(f"duplicate IDs: {', '.join(sorted(dupes))}")
    missing = [r["req_id"] for r in requirements if not str(r.get("location") or "").strip()]
    if missing:
        problems.append(f"no location: {', '.join(missing)}")
    return _result("G2", problems)


def gate_g4(requirements: Sequence[Mapping]) -> GateResult:
    """Every active requirement has a compliance value."""
    missing = [r["req_id"] for r in requirements if r.get("status", "Active") == "Active" and not r.get("compliance")]
    return _result("G4", [f"no compliance value: {', '.join(missing)}"] if missing else [])


def gate_g5(flags: Iterable[Mapping], exception_keys: Iterable[tuple[str, str]]) -> GateResult:
    """Every Block and Warn policy flag has an entry for the Exceptions section (proposal section 9).

    flags: {flag_type, severity, rule_id, req_id}; exception_keys: (rule_id, req_id) pairs present in section 9.
    """
    present = set(exception_keys)
    missing = sorted({(f["rule_id"], f["req_id"]) for f in flags
                      if f.get("flag_type") == "POLICY-RULE" and f.get("severity") in ("Block", "Warn")} - present)
    return _result("G5", [f"no exception entry for {r} on {q}" for r, q in missing])


def gate_g6(page_count: int | None, page_limit: int | None, word_counts: Mapping[str, int], word_limit: int | None,
            required_forms: Iterable[str], provided_forms: Iterable[str], unresolved_ocr: Iterable[str] = ()) -> GateResult:
    """Governed page count <= page limit; every per-answer word count <= limit; every required form accounted for;
    no requirement still carries an unverified low-confidence OCR reading (ARCHITECTURE.md 6.3, ADR-0025)."""
    problems = []
    unverified = sorted(set(unresolved_ocr))
    if unverified:
        problems.append(f"OCR text not verified: {', '.join(unverified)}")
    if page_limit is not None and (page_count is None or page_count > page_limit):
        problems.append(f"governed narrative is {page_count} pages, limit {page_limit}")
    if word_limit is not None:
        over = sorted(k for k, v in word_counts.items() if v > word_limit)
        if over:
            problems.append(f"over {word_limit} words: {', '.join(over)}")
    missing_forms = sorted(set(required_forms) - set(provided_forms))
    if missing_forms:
        problems.append(f"forms not accounted for: {', '.join(missing_forms)}")
    return _result("G6", problems)
