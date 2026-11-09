"""count_words: word counts under the buyer's own counting rule (ARCHITECTURE.md section 6.4)."""

import re
from enum import Enum


class CountingRule(str, Enum):
    # RFP C column I: =IF(LEN(TRIM(H))=0, 0, LEN(TRIM(H)) - LEN(SUBSTITUTE(TRIM(H)," ","")) + 1)
    EXCEL_BUYER_FORMULA = "excel_buyer_formula"
    WHITESPACE = "whitespace"


_SPACE_RUN = re.compile(" {2,}")


def excel_trim(text: str) -> str:
    """Excel TRIM: removes leading/trailing spaces and collapses internal runs to one.

    Only the 7-bit ASCII space (U+0020) is affected. Tabs, line breaks and the nonbreaking
    space (U+00A0) are left in place, exactly as Excel does.
    """
    return _SPACE_RUN.sub(" ", text.strip(" "))


def count_words(text: str | None, rule: CountingRule | str = CountingRule.EXCEL_BUYER_FORMULA) -> int:
    rule = CountingRule(rule)
    text = text or ""
    if rule is CountingRule.EXCEL_BUYER_FORMULA:
        trimmed = excel_trim(text)
        if not trimmed:
            return 0
        return trimmed.count(" ") + 1
    return len(text.split())
