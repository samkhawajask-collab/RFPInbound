from datetime import date

from rfp_tools import Certification, check_certifications


def _by_id(statuses):
    return {s.cert_id: s for s in statuses}


def test_iso9001_expired_for_rfp_a_submission(certifications, opportunities):
    # Section 8.3: CERT-03 (ISO 9001:2015) expired 2026-06-30; RFP A is due 2026-11-20.
    due = date.fromisoformat(opportunities["A"]["hrs_duedate"])
    status = _by_id(check_certifications(certifications, due))
    assert status["CERT-03"].valid is False
    assert status["CERT-03"].expires == date(2026, 6, 30)


def test_other_certificates_valid_for_all_pilot_due_dates(certifications, opportunities):
    for opp in opportunities.values():
        status = _by_id(check_certifications(certifications, date.fromisoformat(opp["hrs_duedate"])))
        assert {cid for cid, s in status.items() if not s.valid} == {"CERT-03"}


def test_uses_submission_date_not_today():
    # Valid today, expired by the submission date: must be reported invalid.
    cert = Certification("C", "Example", issued=date(2024, 1, 1), expires=date(2026, 11, 1))
    [status] = check_certifications([cert], submission_date=date(2026, 11, 20))
    assert status.valid is False


def test_boundaries_are_inclusive():
    cert = Certification("C", "Example", issued=date(2026, 1, 1), expires=date(2026, 12, 31))
    assert check_certifications([cert], date(2026, 12, 31))[0].valid
    assert check_certifications([cert], date(2026, 1, 1))[0].valid
    assert not check_certifications([cert], date(2027, 1, 1))[0].valid
    assert not check_certifications([cert], date(2025, 12, 31))[0].valid


def test_output_order_is_stable():
    certs = [
        Certification("CERT-02", "b", date(2020, 1, 1), date(2030, 1, 1)),
        Certification("CERT-01", "a", date(2020, 1, 1), date(2030, 1, 1)),
    ]
    assert [s.cert_id for s in check_certifications(certs, date(2026, 1, 1))] == ["CERT-01", "CERT-02"]
