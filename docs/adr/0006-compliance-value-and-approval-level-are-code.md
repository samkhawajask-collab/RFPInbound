# ADR-0006 — Compliance value, SME routing and approval level are code

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0005](0005-policy-engine-two-stage-deterministic-then-adjudicated.md)

## Context

Three derived values decide what the buyer is told and who signs it off:

1. the **compliance value** per requirement, one of Full / Partial / No / Clarify / Gap / Withdrawn;
2. the **SME** a gap routes to;
3. the **approval level**, L1-L4.

All three are functions of facts the system already holds. None requires language understanding. All
three are scored by the evaluation harness, so all three must be reproducible across runs.

The interactions are not obvious, which is why they are worth an ADR rather than being left to
whoever writes the stage flow. Severity does not map to compliance one-for-one, and the library
issues (stale, conflicting, retired-match, draft-present) look like they should affect compliance
but must not.

## Options considered

### Option A — Let the drafting agent set the compliance value

It already has the requirement, the answer and the flags.

- **For:** No extra component; the agent has the context.
- **Against:** The same requirement could get different values on two runs, which makes the
  compliance-accuracy metric meaningless and the audit trail unexplainable. Precedence between a
  Block rule and a contradiction is a policy decision, not a drafting decision.

### Option B — Configure the rules in Dataverse and interpret them at run time

Store the precedence table and the approval matrix as data.

- **For:** Changing policy needs no code change.
- **Against:** Over-general for three small, stable functions. The approver matrix is already data
  (`hrs_approver`); the *precedence* is logic and belongs in a tested function.

### Option C — Deterministic functions in `rfp-tools`, unit-tested against the documented rules *(chosen)*

- **For:** One place, one behavior, testable before any agent exists. The tests can be written in
  slice 0 straight from the documented rules, so the logic is verified before the pipeline that uses
  it exists.
- **Against:** A policy change is a code change and a deployment.

## Decision

**Option C.** Three functions: `derive_compliance`, `route_sme`, `derive_approval_level`.

### Compliance precedence

```
1. status == Withdrawn                        -> Withdrawn
2. out of scope OR any Block rule fired        -> No
3. ambiguous OR in a contradiction group       -> Clarify
4. any Warn rule fired                         -> Partial
5. no approved library answer                  -> Gap
6. otherwise                                   -> Full
```

> **Amended 2026-10-29 (Slice 2).** "No approved library answer → Gap" moved from step 2 to step 5.
> The original order reproduced every RFP's expected *counts* but not the expected value *per
> requirement*: the first scored run of RFP A (`eval/reports/20261029-191138-A-dev.md`) had three
> requirements with a fired Block rule and no selected answer, expected **No** but derived as **Gap**.
> Across the answer key, Gap occurs only when nothing else applies; requirements with no library answer
> but a Block rule, a clarification or a Warn rule are expected No, Clarify or Partial.
> The business reason holds on its own: a fired Block rule already states the company's position
> (the rule's approved position), and a clarification is asked before anything is answered, so neither
> needs a library answer. A gap now means only "nothing else applies and no approved answer exists".
> The counts above are still reproduced. Gap routing and the `NO-LIBRARY-ANSWER` flag follow the
> compliance value: stage 7 routes and flags only requirements whose compliance is Gap.

This was derived from the data set's documented compliance logic and then **validated against all
four RFPs' expected compliance counts**, which it reproduces exactly:

| RFP | Full | Partial | No | Clarify | Gap | Withdrawn |
|---|---|---|---|---|---|---|
| A | 46 | 0 | 5 | 2 | 7 | 0 |
| B | 72 | 2 | 2 | 3 | 11 | 0 |
| C | 87 | 2 | 4 | 3 | 14 | 0 |
| D | 37 | 1 | 2 | 3 | 7 | 1 |

Two orderings are load-bearing and were each checked against a specific row:

- **Block outranks Clarify.** RFP A's CV-037 and CV-039 contradict each other, but CV-039 also fires
  a Block rule (R-09). The expected count of Clarify = 2 requires CV-039 to be **No**, leaving only
  CV-037 and CV-038 as Clarify. A naive "contradiction wins" rule would give 3.
- **Clarify outranks Partial.** RFP D's THPA-040 fires a Warn rule (R-33) *and* sits in a
  contradiction group. The expected count of Partial = 1 requires it to be **Clarify**, leaving
  THPA-048 as the only Partial.

**Library issues never change compliance.** Stale, conflicting, retired-match and draft-present
change which answer may be used and whether SME review is required — nothing else. Encoding this
wrongly is the single easiest way to fail the compliance-accuracy metric, because 54 of the 193
expected flags are `LIBRARY-STALE` alone.

**Info-severity rules never change compliance.** R-26 (HIPAA/BAA) and R-34 (subcontracting
disclosure) are Info; their requirements remain Full and the approved position is carried into the
response text.

### SME routing

```
sme = category.primarySME
if any Block or Warn rule fired:
    sme = escalationOwner(worst fired rule)     # Block ranks before Warn
# Info-severity rules never override the category SME
```

### Approval level

```
levelFromValue = L1 if band <  $250k
                 L2 if band in [$250k, $1M]
                 L3 if band >  $1M
                 L4 if band >  $3M

levelFromRisk  = L1 if no Warn and no Block flags
                 L2 if any Warn flag
                 L3 if any Block flag or legal exception
                 L4 if a Block flag is accepted as an exception

approvalLevel  = max(levelFromValue, levelFromRisk)
```

Validated against the pilot data's expected level of **L3 for all four RFPs**: A and D reach L3 by
both routes (value band above $1M and Block flags present), while B and C reach L3 by the risk route
alone — their value bands would otherwise give L2. A design that used only the value band would get
B and C wrong.

Approver identity is read from `hrs_approver`, keyed on `hrs_level`. The function returns a level,
never a person.

## Consequences

- These three functions are built and unit-tested in slice 0, before any agent exists, with test
  cases taken directly from the documented rules and the four RFPs' expected counts.
- Compliance accuracy has a 0.95 threshold (0.92 for RFP D, where extraction noise propagates). A
  systematic miss points at the precedence table, not at the model.
- A policy change — a new severity, a new value band — is a code change with a test, and an ADR
  amendment.
- `hrs_requirement.hrs_compliance` is only ever written by `derive_compliance`. No agent has write
  access to that column.
- The approval card's counts are a projection of this column, so the approver and the compliance
  matrix can never disagree.
