"""Hard rule 1, layer 4 (ARCHITECTURE.md section 10.3): only eval/ may reference gold data.

Fails if any file under tools/, scripts/, solution/ or assets/ mentions a gold-data path. The
forbidden strings are assembled at run time so this file does not match itself.

Besides the gold-data folder and the gold CSV, the dataset's root README (it indexes each RFP's answer key)
and the generator folder (it computes the answer key) are treated as gold (ARCHITECTURE.md 10.3).
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("tools", "scripts", "solution", "assets")
DATASET = "RFP_Synthetic_" + "Dataset_Pilot"
FORBIDDEN = (
    "08_" + "Gold_Data",
    "90_eval_" + "goldrequirement",
    "gen_" + "gold",
    "10_" + "Generator_Source",
    DATASET + "/" + "README",
    DATASET + "\\" + "README",
)
SKIP_PARTS = {"__pycache__", ".pytest_cache", "build"}


def _scanned_files():
    for d in SCANNED_DIRS:
        root = REPO_ROOT / d
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and not SKIP_PARTS.intersection(path.parts):
                yield path


def test_no_gold_references_outside_eval():
    offenders = []
    for path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        offenders += [f"{path.relative_to(REPO_ROOT)}: {s}" for s in FORBIDDEN if s.lower() in text]
    assert not offenders, "Gold-data references outside eval/:\n" + "\n".join(offenders)


def test_nothing_outside_eval_imports_the_harness():
    """The harness reads gold; importing it from pipeline code would carry gold access with it."""
    marker = "rfp_" + "eval"
    offenders = [str(p.relative_to(REPO_ROOT)) for p in _scanned_files()
                 if p.suffix in (".py", ".ps1", ".json", ".js") and marker in p.read_text(encoding="utf-8", errors="ignore")]
    assert not offenders, "Harness referenced outside eval/:\n" + "\n".join(offenders)
