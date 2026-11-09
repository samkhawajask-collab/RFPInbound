import pytest

from rfp_tools import CountingRule, count_words
from rfp_tools.words import excel_trim

EXCEL = CountingRule.EXCEL_BUYER_FORMULA


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        (None, 0),
        ("   ", 0),
        ("one", 1),
        ("one two three", 3),
        ("  leading and trailing  ", 3),
        # Excel TRIM collapses internal space runs, so a double space does not inflate the count.
        ("double  space", 2),
        # Tabs and line breaks are not spaces to Excel: the words either side merge into one.
        ("tab\tseparated words", 2),
        ("line\nbreak here", 2),
        ("crlf\r\nbreak", 1),
        # Nonbreaking space (U+00A0) is untouched by TRIM and is not counted as a separator.
        ("non breaking space", 2),
    ],
)
def test_excel_buyer_formula(text, expected):
    assert count_words(text, EXCEL) == expected


def test_default_rule_is_the_buyer_formula():
    assert count_words("tab\tseparated") == 1


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 0), ("tab\tseparated words", 3), ("line\nbreak here", 3), ("double  space", 2)],
)
def test_whitespace_rule(text, expected):
    assert count_words(text, "whitespace") == expected


def test_rules_diverge_on_line_breaks():
    text = "Item one\nItem two"
    assert count_words(text, EXCEL) == 3
    assert count_words(text, CountingRule.WHITESPACE) == 4


def test_excel_trim_only_touches_ascii_space():
    assert excel_trim("  a   b\t\tc  ") == "a b\t\tc"


def test_unknown_rule_rejected():
    with pytest.raises(ValueError):
        count_words("x", "split_on_commas")
