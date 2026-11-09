"""check_certifications: certificate validity on the submission date (ARCHITECTURE.md section 8.3).

The register's hrs_statusasof20261027 column is a snapshot and is deliberately ignored.
A certificate is valid when issued <= submission date <= expires; both ends are inclusive
(assumption: a certificate is valid through its expiry date).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Certification:
    cert_id: str
    name: str
    issued: date
    expires: date


@dataclass(frozen=True)
class CertificationStatus:
    cert_id: str
    name: str
    expires: date
    valid: bool


def check_certifications(certs: Iterable[Certification], submission_date: date) -> list[CertificationStatus]:
    return [
        CertificationStatus(
            cert_id=c.cert_id,
            name=c.name,
            expires=c.expires,
            valid=c.issued <= submission_date <= c.expires,
        )
        for c in sorted(certs, key=lambda c: c.cert_id)
    ]
