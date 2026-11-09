"""Choice values and Dataverse naming, read from the same spec that generates the schema (ADR-0014).

At deploy time scripts/deploy-tools.ps1 copies solution/spec/tables.json into the package as
rfp_tools/tables.json; in the repo the spec is read in place. Either way there is one source.
"""

import json
from functools import lru_cache
from pathlib import Path

_PACKAGED = Path(__file__).with_name("tables.json")
_REPO = Path(__file__).resolve().parents[2] / "solution" / "spec" / "tables.json"


@lru_cache(maxsize=1)
def spec() -> dict:
    path = _PACKAGED if _PACKAGED.exists() else _REPO
    return json.loads(path.read_text(encoding="utf-8"))


def choice_value(choice: str, label: str) -> int:
    for value, text in spec()["choices"][choice]["options"]:
        if text == label:
            return value
    raise ValueError(f"{label!r} is not an option of {choice}")


def choice_label(choice: str, value: int | None) -> str | None:
    if value is None:
        return None
    for v, text in spec()["choices"][choice]["options"]:
        if v == int(value):
            return text
    raise ValueError(f"{value!r} is not a value of {choice}")


def entity_set(table: str) -> str:
    """Default Dataverse entity set names for this spec's tables (plural of the logical name)."""
    if table.endswith("y") and not table.endswith(("ey", "ay", "oy")):
        return table[:-1] + "ies"
    if table.endswith(("s", "x", "ch", "sh")):
        return table + "es"
    return table + "s"
