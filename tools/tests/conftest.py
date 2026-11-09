"""Fixtures loaded from the reference CSVs only. Gold data is never read here (hard rule 1)."""

import csv
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPORT_DIR = REPO_ROOT / "data" / "RFP_Synthetic_Dataset_Pilot" / "09_Dataverse_Import"


def read_reference_csv(name: str) -> list[dict[str, str]]:
    with open(IMPORT_DIR / name, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="session")
def categories() -> list[dict[str, str]]:
    return read_reference_csv("01_hrs_category.csv")


@pytest.fixture(scope="session")
def policy_rules() -> dict[str, dict[str, str]]:
    return {r["hrs_ruleid"]: r for r in read_reference_csv("04_hrs_policyrule.csv")}


@pytest.fixture(scope="session")
def opportunities() -> dict[str, dict[str, str]]:
    return {r["hrs_rfpkey"]: r for r in read_reference_csv("06_hrs_opportunity.csv")}


@pytest.fixture(scope="session")
def certifications():
    from rfp_tools import Certification

    return [
        Certification(
            cert_id=r["hrs_certid"],
            name=r["hrs_name"],
            issued=date.fromisoformat(r["hrs_issued"]),
            expires=date.fromisoformat(r["hrs_expires"]),
        )
        for r in read_reference_csv("07_hrs_certification.csv")
    ]
