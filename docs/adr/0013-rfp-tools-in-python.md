# ADR-0013 — `rfp-tools` is written in Python

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0006](0006-compliance-value-and-approval-level-are-code.md), [ADR-0009](0009-evaluation-harness-outside-the-platform.md)

## Context

§3.5 defines `rfp-tools`: one codebase under `tools/` holding every operation where exactness matters
(hard rule 6). It's exposed as a custom connector for workflows and as an MCP server for agents.
Slice 0 needs five of those operations and their unit tests: `count_words`, `check_certifications`,
`derive_compliance`, `route_sme` and `derive_approval_level`. Hosting is not needed until Slice 1.

The choice of language matters for three reasons. The same rules are re-implemented by the
evaluation harness when it checks outputs, so sharing code with `eval/` avoids two implementations
drifting apart. The dataset generator (`10_Generator_Source/`) is already Python. And the hosting
option has to be something the custom connector and an MCP server can both front.

## Options considered

### Option A — Python 3.12 *(chosen)*

- **For:** Same language as the dataset generator and the planned `eval/` harness, so `eval/` can
  import `rfp_tools` directly instead of re-implementing the rules. Mature MCP SDK, and Azure
  Functions supports Python for the connector's back end. The document-processing libraries the
  later slices need (Open XML, PDF and the Document Intelligence SDK) are first-class in Python.
  Python 3.12 and pytest are already installed on the build machine.
- **Against:** A second runtime alongside PowerShell scripts. Cold starts on a consumption-plan
  Azure Function are slower than for .NET.

### Option B — TypeScript on Node

- **For:** Strong MCP SDK; Node 24 is installed; one language with any future PCF or custom visual.
- **Against:** The generator and the harness would use a different language, so the rule
  implementations would be duplicated or called across a process boundary.

### Option C — C# (.NET)

- **For:** Best Azure Functions cold-start profile; the Dataverse SDK is native.
- **Against:** No other code in the project is .NET, and the document libraries the later slices
  need are less convenient.

## Decision

**Option A.**

- `tools/rfp_tools/` is a pure-function library with no I/O and no model calls. Its enum values
  match the Dataverse choice labels.
- Unit tests live in `tools/tests/`, built only from the documented rules and the reference CSVs.
  `tools/tests/test_gold_isolation.py` enforces hard rule 1's code layer for `tools/`, `scripts/`
  and `solution/`.
- **Hosting is deferred to Slice 1.** The connector back end and the MCP server will be thin
  wrappers over this library. Whether one artifact can serve both surfaces is still the open
  §3.5 [UNVERIFIED] item. The tenant has an Azure subscription ("Subscription 1"), which makes an
  Azure Function feasible but doesn't settle Q1.

## Consequences

- `eval/` (Slice 2) can import `rfp_tools` and score against exactly the code the pipeline runs.
- Assumptions made where the docs are silent are written down in the module docstrings:
  - certificate validity is inclusive at both ends;
  - SME routing breaks ties between equally severe rules on the lowest rule ID;
  - value bands are placed by their lower bound.
- Any rule change is a code change with a test, never an instruction change.
