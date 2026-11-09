"""Section 9.3, checked against the reference approver matrix and opportunity value bands."""

import pytest

from rfp_tools import ApprovalLevel, derive_approval_level, value_band_lower_bound
from tests.conftest import read_reference_csv


@pytest.mark.parametrize(
    ("band", "expected"),
    [
        ("< $250,000", ApprovalLevel.L1),
        ("$250,000 - $1,000,000", ApprovalLevel.L2),
        ("$1,000,000 - $3,000,000", ApprovalLevel.L3),
        ("> $1,000,000", ApprovalLevel.L3),
        ("> $3,000,000", ApprovalLevel.L4),
    ],
)
def test_level_from_value_alone(band, expected):
    assert derive_approval_level(band, []) is expected


def test_approver_matrix_bands_map_to_their_own_level():
    for row in read_reference_csv("05_hrs_approver.csv"):
        assert derive_approval_level(row["hrs_valueband"], []).value == row["hrs_level"]


@pytest.mark.parametrize(
    ("severities", "kwargs", "expected"),
    [
        (["Info"], {}, ApprovalLevel.L1),
        (["Warn"], {}, ApprovalLevel.L2),
        (["Warn", "Block"], {}, ApprovalLevel.L3),
        ([], {"legal_exception": True}, ApprovalLevel.L3),
        (["Block"], {"block_accepted_as_exception": True}, ApprovalLevel.L4),
    ],
)
def test_level_from_risk(severities, kwargs, expected):
    assert derive_approval_level("< $250,000", severities, **kwargs) is expected


def test_pilot_opportunities_are_l3(opportunities):
    # Section 9.3: A and D reach L3 by value and by Block flags; B and C by the risk route alone.
    for key in "AD":
        assert derive_approval_level(opportunities[key]["hrs_valueband"], []) is ApprovalLevel.L3
    for key in "BC":
        band = opportunities[key]["hrs_valueband"]
        assert derive_approval_level(band, []) is ApprovalLevel.L2
        assert derive_approval_level(band, ["Block"]) is ApprovalLevel.L3
    for key in "ABCD":
        assert derive_approval_level(opportunities[key]["hrs_valueband"], ["Warn", "Block"]) is ApprovalLevel.L3


def test_unparseable_band_rejected():
    with pytest.raises(ValueError):
        value_band_lower_bound("TBD")
