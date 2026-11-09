# Supplementary reference data

Data this solution needs that the synthetic dataset doesn't supply in machine-readable form. It's
seeded into Dataverse alongside `RFP_Synthetic_Dataset_Pilot/09_Dataverse_Import` by
`scripts/seed-data.ps1`. Company-specific facts live here, as data, never in code (CLAUDE.md).

| File | Seeds | Source |
|---|---|---|
| `policy_rule_thresholds.csv` | `hrs_policyrule.hrs_threshold*` columns | Transcribed from each rule's written `hrs_condition` in `04_hrs_policyrule.csv`. These are the 14 rules with a numeric threshold (ARCHITECTURE.md section 8.2). |

## Threshold semantics

A rule's threshold is **breached** when the value the buyer requires compares to the threshold with
`hrs_thresholdoperator`:

- `gt`: the requirement demands more than the threshold. Example: R-02 fires when the required
  availability is above 99.9%.
- `lt`: the requirement demands less than the threshold. Example: R-12 fires when the required RPO
  is under 1 hour.

`wcag_level` is encoded as `version + level/100`, with A = 1, AA = 2 and AAA = 3. So WCAG 2.1 AA is
2.12, and R-28's condition, "above WCAG 2.1 AA", is `gt 2.12`.

A breach is evidence for the adjudication prompt, not a verdict: the prompt still reads the rule's
condition. The model never does the arithmetic (ARCHITECTURE.md section 8.2).
