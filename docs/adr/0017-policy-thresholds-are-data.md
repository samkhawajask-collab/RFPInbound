# ADR-0017 — Policy rule thresholds are data columns, not code

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0005](0005-policy-engine-two-stage-deterministic-then-adjudicated.md), [ADR-0014](0014-dataverse-schema-from-spec.md)

## Context

14 of the 35 policy rules carry a numeric threshold in their written `hrs_condition`, for example
"Required availability commitment exceeds 99.9% monthly". §8.2 requires that the comparison is
arithmetic in code (`evaluate_thresholds`), never model judgment. But the thresholds are company
policy, and CLAUDE.md requires company facts to live in data, never in code. The reference schema
(`Dataverse_Schema.md`) has no column for a machine-readable threshold.

## Options considered

- **A — Hard-code a rule → threshold map in rfp-tools.** Simple, but it puts company policy in code
  and silently diverges when the register changes.
- **B — Let the adjudication prompt compare the numbers.** This breaks §8.2's "the model never does
  the arithmetic".
- **C — Add threshold columns to `hrs_policyrule`, seeded from a supplementary CSV** *(chosen)*.

## Decision

**Option C.**

- **New columns on `hrs_policyrule`:**
  - `hrs_thresholdmetric` (text): one of the metrics `evaluate_thresholds` knows, such as
    `availability_percent` or `rpo_hours`.
  - `hrs_thresholdoperator` (text): `gt` or `lt`.
  - `hrs_thresholdvalue` (decimal).
  - `hrs_thresholdunit` (text).
- **Source:** `data/supplement/policy_rule_thresholds.csv`, transcribed from each rule's written
  condition. `data/supplement/README.md` documents its semantics, including the WCAG level encoding.
  `scripts/seed-data.ps1` merges it into `hrs_policyrule`.
- **Flow:** the stage 6 flow reads the thresholds from Dataverse and passes them to
  `match_rule_candidates` and `evaluate_thresholds`. rfp-tools holds only metric *extractors* (how
  to read "15 minutes" as 0.25 hours) and no threshold values.
- **A breach is evidence for the adjudication prompt, not a verdict.** The prompt still reads the
  condition, per ADR-0005.

## Consequences

- When the register changes a threshold, the change is a data edit plus re-seed, not a code release.
- A rule with a new kind of number needs a new metric extractor in code, which is the honest
  boundary: reading units is code, and the values are policy.
- The reference schema is extended, not altered. A10 still holds for the dataset's own columns.
