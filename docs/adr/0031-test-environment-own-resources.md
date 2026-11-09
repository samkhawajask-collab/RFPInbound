# ADR-0031 — The test environment has its own rfp-tools and SharePoint site, and no evaluation tables

- **Status:** Accepted
- **Date:** 2026-11-01
- **Deciders:** The user chose separate resources; solution architecture proposed dropping the evaluation tables, in the plan the user approved
- **Related:** [ADR-0007](0007-gold-data-isolation-by-architecture-not-by-prompt.md), [ADR-0009](0009-evaluation-harness-outside-the-platform.md), [ADR-0023](0023-document-intelligence-from-rfp-tools-with-managed-identity.md)

## Context

Slice 7 was installed in test with nothing wired up. Test needs an rfp-tools endpoint and a SharePoint site before it
can run the pipeline. The scripts knew only dev's Azure and SharePoint names, and `eval/run.ps1 -Target test` would
have uploaded to dev's site and reset dev's run.

ARCHITECTURE.md and ADR-0009 also describe `hrs_evalrun`, `hrs_evalresult` and `hrs_evalgoldrequirement` in test, in a
`RFPFactoryEval` solution. They were never built. The harness reads the answer key from files and writes reports locally.

## Decision

**Test gets its own resources.** They are a resource group, storage account, Flex Consumption Function App, Document
Intelligence resource and SharePoint site, named in `scripts/env.config` with a `TEST_` prefix.
`Get-TargetResources` returns the names for a target and throws if one is missing, so a test command can't fall back
to dev's resources. Scripts take `-Target dev|test`, defaulting to dev.

**No evaluation tables in test.** The answer key stays in files that only `eval/` reads. Scores stay in `eval/reports/`.

## Alternatives

- **Share dev's rfp-tools and site.** Cheaper, but test runs would write to dev's libraries, and a pass would prove less.
- **Build `RFPFactoryEval`.** Would give queryable score history in Dataverse. It would also put the answer key in an
  environment the agents run in, which adds a leak risk (risk R10) and doesn't change any score.

## Results (test, 2026-11-01)

`./eval/run-all.ps1 -Target test`, all three passing with zero must-not-use violations:

| RFP | Answer ID | Compliance | Flag precision | Machine minutes | Credits |
|---|---|---|---|---|---|
| A | 0.980 | 1.000 | 0.971 | 3.8 | 514.4 |
| B | 0.987 | 0.989 | 0.891 | 5.2 | 707.7 |
| D | 0.927 | 1.000 | 0.923 | 8.8 | 914.7 |

Isolation held. Dev's Function App recorded 0 executions from 11:00 UTC through the run, against 149 for test's. Dev's
RFP rows and its Inbound and Output/Draft files were unchanged. Test has no `hrs_eval*` table.

## Consequences

- Two sets of Azure resources and two sites to maintain. rfp-tools must be deployed to both.
- This supersedes the parts of ADR-0009 and ARCHITECTURE.md that have the harness write evaluation tables.
- A test run touches only test's resources. The acceptance run checks this.
- Test is shared with Project Operations, whose upgrades can block imports (docs/RUNBOOK.md §11).
