# ADR-0009 — Evaluation harness outside the platform, and initial thresholds

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0007](0007-gold-data-isolation-by-architecture-not-by-prompt.md)

## Context

No format slice is done until it meets its evaluation thresholds with zero must-not-use violations.
That makes the harness a gating component, not a reporting afterthought.

What has to be measured: extraction recall and precision, expected answer ID accuracy, must-not-use
violations, flag recall and precision by type, compliance value accuracy, gap routing accuracy,
clarification coverage, addendum handling, and limit compliance — against an answer key of 311
requirement rows, 193 expected flags, 39 gaps, 8 clarifications and 4 addendum changes.

Copilot Studio has a built-in **Evaluate** capability (preview). Its only test method is *General
quality* — an AI-judged assessment of relevance and completeness, scored Pass / Fail / Invalid /
Error, over conversations with optional expected responses.

## Options considered

### Option A — Copilot Studio's built-in Evaluate

- **For:** No code; in-product; results visible to makers; useful version-to-version comparison.
- **Against:** Cannot compute recall against a 311-row key, cannot count must-not-use violations,
  cannot compare a six-value compliance choice, cannot check a page count. Its unit is a
  *conversation*, and our unit is a *requirement*. Worse, it would need the answer key inside the
  environment where the agents run, which [ADR-0007](0007-gold-data-isolation-by-architecture-not-by-prompt.md)
  forbids.

### Option B — Evaluation as a Power Automate flow inside the environment

Read gold from a Dataverse table, compute metrics, write results.

- **For:** Stays in the platform; same tooling as the rest of the solution.
- **Against:** Puts the answer key in the agents' environment, defeating layers 1-3 of the isolation
  design. Flow expressions are a poor fit for set arithmetic and text similarity.

### Option C — An external runner under `eval/` *(chosen)*

Reads run output through the Dataverse Web API, reads the answer key from the file system, computes
metrics, writes `hrs_evalrun` / `hrs_evalresult` and a report.

- **For:** The answer key never enters the environment. Set arithmetic, similarity matching and
  report generation are ordinary code with ordinary tests. Runs in CI. Gold-data access is confined
  to one directory, which the CI check enforces.
- **Against:** A second toolchain to maintain, and it needs credentials to the test environment.

## Decision

**Option C.** The harness lives in `eval/`. Copilot Studio's Evaluate tab is kept for conversational
smoke tests of the `RFP Orchestrator` agent only, and is never used for scoring against gold.

### Run contract

```
scripts/seed-data.ps1          # one-time: load reference CSVs into the test environment
eval/run.ps1 -Rfp A            # or -Rfp All
```

`eval/run.ps1`:

1. resets run state for the chosen RFPs in the test environment;
2. places the source documents in the inbound library and waits for `RFP - Run Pipeline` to reach
   stage 9 or a gate failure;
3. reads the runtime tables through the Dataverse Web API;
4. reads the answer key from `08_Gold_Data` — the only code permitted to do so;
5. computes the metrics, writes `hrs_evalrun` and `hrs_evalresult`;
6. emits `eval/reports/<timestamp>-<rfp>.md` and the matching `.json`.

### Requirement matching rule

- RFPs **A, C, D** — match on `req_id`, because the buyer prints IDs.
- RFP **B** — the PDF contains no IDs, so `MCHD-###` is ours. A match requires the same `location`
  **and** text similarity above a configured threshold against `requirement_text`. B's matching is
  reported separately so an ID-ordering difference is not mistaken for a recall failure.

### Report order

Pass/fail against thresholds; must-not-use violations listed individually (any non-zero count fails
the run outright, regardless of every other score); the per-metric table; a confusion list of missed
and spurious requirements; a per-flag-type breakdown; estimated Copilot Credit consumption.

### Initial thresholds for `eval/thresholds.json`

Set per format, because the difficulty differs by format rather than by metric alone.

| Metric | A (docx) | B (PDF prose) | C (xlsx) | D (scanned) | Rationale |
|---|---|---|---|---|---|
| Extraction recall | 1.00 | 0.92 | 1.00 | 0.90 | A and C are structurally explicit — anything below 1.00 is a parser bug. B has no IDs and three appendix numbering styles. D loses ~6% of lines to OCR. |
| Extraction precision | 0.98 | 0.92 | 1.00 | 0.92 | Prose invites spurious splits; Excel rows cannot be invented. |
| Answer ID accuracy | 0.90 | 0.90 | 0.90 | 0.85 | Driven by rerank quality inside the filtered set; D inherits extraction noise. |
| **Must-not-use violations** | **0** | **0** | **0** | **0** | Structurally guaranteed by ADR-0004. Non-zero means the filter was bypassed. |
| Compliance accuracy | 0.95 | 0.95 | 0.95 | 0.92 | The precedence table is deterministic; residual error is upstream. |
| Gap routing accuracy | 0.90 | 0.90 | 0.90 | 0.90 | Routing is deterministic given the category; error comes from category misclassification. |
| Clarification coverage | 0.75 | 0.75 | 0.75 | 0.75 | 2 questions per RFP; 0.75 means at least one of two, and 6 of 8 overall. |
| Flag precision (overall) | 0.85 | 0.85 | 0.85 | 0.85 | False flags cost SME and approver time; see ADR-0005. |
| Addendum assertions | n/a | n/a | n/a | 6 / 6 | Four requirement changes plus the due date and the page limit. All or nothing. |
| Limit breaches | 0 | 0 | 0 | 0 | A breach makes the proposal non-responsive. |

Flag **recall** is set per type, because the types differ in kind. Deterministic flags must be
perfect; judgment flags get room:

| Flag type | Basis | Recall |
|---|---|---|
| `LIBRARY-STALE` | Date arithmetic | **1.00** |
| `CERTIFICATION-INVALID` | Date arithmetic | **1.00** |
| `LIBRARY-RETIRED-MATCH` | Register query + topic judgment (ADR-0019) | 0.80 (amended 2026-10-29) |
| `LIBRARY-DRAFT-PRESENT` | Register query + topic judgment (ADR-0019) | 0.80 (amended 2026-10-29) |
| `ADDENDUM-CHANGE`, `ADDENDUM-WITHDRAWN` | Set diff | **1.00** |
| `NO-LIBRARY-ANSWER` | Query result | 0.95 |
| `POLICY-RULE` | Match plus adjudication | 0.90 |
| `LIBRARY-CONFLICT` | Comparison plus judgment | 0.80 |
| `OUT-OF-SCOPE` | Judgment | 0.80 |
| `DUPLICATE` | Judgment | 0.80 |
| `CONTRADICTION` | Judgment | 0.75 |
| `AMBIGUOUS` | Judgment | 0.75 |

The 1.00 values are not aspirational. They are the test that the deterministic layers are actually
deterministic: a score below 1.00 on a date-arithmetic flag is a defect report, not a tuning
opportunity.

## Consequences

- Slice 2 (the harness) follows slice 1 rather than preceding it, because the harness needs a real
  run output shape to read. Building it against a hypothetical shape would be rewritten.
- A full four-RFP evaluation costs roughly 4,000 Copilot Credits, so development runs are
  single-RFP and the report carries the consumption estimate.
- Thresholds are a contract, not a target. Revising one requires an amendment to this ADR naming
  the run that prompted the change and the reason — which stops quiet threshold erosion when a
  slice is nearly passing.
- The harness needs test-environment credentials in CI, supplied as secrets, never committed.
- `hrs_evalrun` / `hrs_evalresult` exist only in the evaluation environment, per ADR-0007.

## Amendment 1 — 2026-10-29: library-trap recall thresholds

**Change.** `LIBRARY-RETIRED-MATCH` and `LIBRARY-DRAFT-PRESENT` recall: 1.00 → **0.80**. Approved by the
user in the Slice 2 session.

**Run that prompted it.** `eval/reports/20261029-201421-A-dev.md`. Every RFP A threshold passed except
`LIBRARY-RETIRED-MATCH`, at 5/6. The miss is CV-013 ("furnish seventy-five rugged tablet devices… and
provide hardware maintenance"). The key expects the Approved answer "What mobile platforms are
supported?" to be cited, with a flag for its Retired predecessor. The pipeline reads CV-013 as a
hardware-supply request: compliance No (correct, R-29 and out of scope), with no library answer cited.

**Reason.** The 1.00 was set because these flags were a register query, "a test that the deterministic
layers are actually deterministic". Slice 2 measured that a deterministic query can't find them.
- Word overlap between a requirement and a Retired or Draft question scores about 0.43–0.52 for true and
  false matches alike, once categories are crossed.
- [ADR-0019](0019-meaning-based-candidate-recall.md) therefore added a topic judgment by the shortlist
  prompt (flag-only).
- The flags now rest partly on judgment, so they get a judgment-type threshold, like `LIBRARY-CONFLICT`
  (0.80).

**What stays at 1.00.** The deterministic parts are still checked exactly. `LIBRARY-STALE` (date
arithmetic) and `CERTIFICATION-INVALID` keep 1.00, and the superseded-by link is covered by unit tests.
Must-not-use stays 0. That is the control the trap flags exist to protect, and it is enforced by
construction: selection accepts only Approved IDs.

## Amendment 2 — 2026-10-29: pass rule for non-deterministic runs

**Change.** A format slice passes when its thresholds are met in **2 of 3 consecutive** scored runs with
no changes between them. Must-not-use violations must be 0 in **every** run. Approved by the user in the
Slice 2 session.

**Run that prompted it.** `eval/reports/20261029-201421-A-dev.md` passed; an unchanged repeat,
`20261029-201916-A-dev.md`, failed gap routing 5/7. The prompts' output varies between runs at
temperature 0, and with 7 gaps one flipped judgment fails a 0.90 threshold. A single passing run is
therefore not evidence of a pass. [ADR-0021](0021-self-consistency-for-judgment-prompts.md) reduces the
variance, and this rule measures what is left.

## Amendment 3 — 2026-10-30: adjudicated answer-key values

**Change.** A requirement whose answer-key value the project disputes can be recorded in
`eval/adjudications.json`, approved one item at a time by the user. Each entry records the key's value,
the pipeline's value and the reason. An adjudicated requirement:
- stays in extraction recall and precision;
- stays in must-not-use, which is never excluded;
- is left out of answer-ID, compliance, gap-routing and flag metrics;
- is listed in every report.

Thresholds are unchanged.

**First entry: RFP A, CV-011** ("support fleet vehicle telematics, including GPS tracking of crew
vehicles").
- The key says Gap. In every scored run the pipeline says out of scope, which gives compliance No,
  because the company scope statement in every product document excludes "vehicle telematics devices".
- Both readings are defensible. Tuning the relation prompt until it agrees with the key would fit the
  answer key.
- Without the entry, CV-011 alone fails gap routing (6/7 against 0.90) and `NO-LIBRARY-ANSWER` recall
  (6/7 against 0.95).

**Runs.** `eval/reports/20261030-013917`, `-014446` and `-015013-A-dev.md` were three unchanged repeats:
- **Repeat 1:** CV-011, plus a missed duplicate (CV-060).
- **Repeats 2 and 3:** CV-011 only.

With the entry applied, repeats 2 and 3 meet every threshold; `20261030-072726-A-dev.md` re-scores
repeat 3 with the adjudication and returns PASS. RFP A meets the 2-of-3 rule (amendment 2) in dev, with
zero must-not-use violations in every run.

**Second entry: RFP A, CV-013** ("furnish seventy-five rugged tablet devices with cellular service and
provide hardware maintenance"). Approved by the user in the Slice 2 session, 2026-10-30.
- The key expects the supported-mobile-platforms answer to be cited, and a LIBRARY-RETIRED-MATCH flag for
  that answer's retired predecessor.
- In every scored run, the pipeline flags the requirement as out of scope (the scope statement excludes
  hardware supply and maintenance) and cites no answer. Compliance (No) matches the key.
- Treating a device-compatibility statement as the response to a hardware-supply demand is a judgment
  call. A prompt change aimed at it (ADR-0019 amendment 1) didn't move it, and it added a spurious flag
  elsewhere. It was reverted.
- The entry doesn't change any verdict. Tuning 7 met the 2-of-3 rule with CV-013 counted as a miss. It
  removes CV-013 from answer-ID accuracy and from LIBRARY-RETIRED-MATCH recall, which had no margin
  (5/6 against 0.8).

## Amendment 4 — 2026-10-30: RFP B matches on location anchors

**Context.** RFP B has no buyer IDs, so the harness matches its requirements on location plus text
similarity (at least 0.6). The match compared location strings exactly. Our locations are derived from
blocks (ARCHITECTURE.md §6.2). Any difference in wording, such as "Section 3.6.2" against "Section 3.6,
paragraph 3.6.2", or a page suffix, lost the match regardless of extraction quality.

**Change.** Both locations are reduced to the place they name (`rfp_eval.matching.anchor`):
- the innermost label: a numbered paragraph (`3.6.2`), a table item (`table 3-1 item a`) or an appendix
  item code (`c-01`, `d.1`);
- or the section plus the position for an unnumbered paragraph.

The page suffix and connector words ("Section", "paragraph", "item") are ignored. Text similarity of at
least 0.6 still decides between requirements that share an anchor. The rule was designed from the
documented location convention and fabricated test cases (`eval/tests/test_metrics.py`), not from the
answer key. Matching by ID for A, C and D is unchanged, and thresholds are unchanged. Approved by the user
with the Slice 3 plan.

**Third entry: RFP B, MCHD-053** (station two full-time staff on site around the clock for the full
contract term). Approved by the user in the Slice 3 session, 2026-10-30.
- The key flags it OUT-OF-SCOPE, meaning it can't be met.
- The company's scope statement excludes hardware only. The relation prompt ties out of scope to that
  statement, so it doesn't flag this requirement. Policy rule R-20 (permanent on-site staffing, Block)
  fires instead, and compliance is No, which matches the key.
- Widening the out-of-scope definition would apply to every RFP and is the opposite direction from
  CV-011.

**Fourth entry: RFP B, MCHD-088** (Appendix E-1, "defend the agency against IP infringement claims").
Approved by the user in the Slice 3 session, 2026-10-30.
- The key marks it a duplicate of Appendix D.4 ("indemnify the agency against third-party IP
  infringement claims").
- Defend and indemnify are distinct contractual duties: the duty to defend covers handling the claim, the
  duty to indemnify covers paying for it.
- Every relation pass in the last three runs judged them separate requirements.

## Amendment 5 — 2026-10-30: flag types with few cases pass with at most one miss

**Context.** RFP B ran nine full times across tuning rounds 4–6 (`eval/reports/20261030-194326` to
`-211224-B-dev.md`).
- **Every run:** must-not-use was 0, and every headline metric passed (extraction, answer ID, compliance,
  gap routing, clarification, flag precision).
- **Every run failed** on one or two per-flag recalls. Each of those flag types has 4 to 11 gold cases:
  LIBRARY-DRAFT-PRESENT 3/4 against 0.80, NO-LIBRARY-ANSWER 10/11 against 0.95, LIBRARY-CONFLICT 3/5.
- With so few cases, one borderline judgment fails the type. Prompt changes moved the miss from one
  requirement to another rather than removing it.

**Change.** A flag type with fewer than **12** gold cases in an RFP also passes when it has **at most one**
miss, whatever the ratio (`small_flag_rule` in `eval/thresholds.json`).
- Types with 12 or more cases keep the ratio only. For example, LIBRARY-STALE (18 cases in RFP B) must
  still be 1.00.
- Must-not-use stays at zero in every run.
- Headline thresholds and the 2-of-3 pass rule don't change.
- Reports show the allowance in the flag table ("≥ 0.8 or ≤ 1 miss").
- Approved by the user in the Slice 3 session.

**Known issue kept visible.** MCHD-067's LIBRARY-DRAFT-PRESENT flag was missed in 8 of the 9 runs. The
draft "Do you guarantee 99.99% uptime?" isn't linked to the requirement "commit to a service availability
level". The allowance doesn't hide it: every report lists it, and it's carried into Slice 7 hardening.

**Result under the amendment.** Tuning 6's three unchanged repeats, read under the rule:
- **Repeat 1** (`20261030-205344`): PASS (draft 3/4, no-library-answer 10/11).
- **Repeat 2** (`-210322`): FAIL (library conflict 3/5, two misses).
- **Repeat 3** (`-211224`): PASS. It was also re-scored live as `-211511`.

Must-not-use was 0 in all three, so **RFP B meets the 2-of-3 rule** in dev. Repeats 1 and 2 were read from
their reports' numerators and denominators, because their run data had been replaced in dev. The pipeline
didn't change between the runs and the re-reading.

**Fifth adjudication: RFP A, CV-053** (commit to a local economic impact plan including local hiring).
Approved by the user in the Slice 3 session, 2026-10-31.
- The key routes the gap to General Counsel (Commercial & Legal).
- Every full run classifies it Company & Experience, which routes to the proposal director. Neither
  category description covers a local-hiring commitment.
- Slice 2's RFP A passes reused one older classification, because they restarted from stage 4 or 5. The
  Slice 3 regression runs (`eval/reports/20261030-212435`, `20261031-095851`, `-100639-A-dev.md`) were
  the first full runs since then.
- With the entry applied, the three regression runs read:
  - **Run 1:** PASS (from its report: gap routing's only miss was CV-053).
  - **Run 2:** FAIL (CV-038's ambiguity missed, so clarification coverage is 1/2 and there's a spurious
    gap).
  - **Run 3:** PASS (re-scored live, `20261031-101638-A-dev.md`).
  - Must-not-use was 0 in all three, so **RFP A still meets the 2-of-3 rule** after Slice 3.

## Amendment 6 — 2026-10-31: RFP D's addendum assertions, OCR surfacing and design-only flags

**Context.** Slice 5 merges the scanned path and addendum handling (the user's decision). The Slice 2
harness checked only withdrawn/active state for addendum items, counted flag types that are not in the
answer key as spurious, and had no way to tell whether an OCR misread was surfaced.

**Change.**
- **`addendum_assertions`** checks all six exit assertions: THPA-036's revised text, THPA-010 withdrawn,
  THPA-020 Mandatory, THPA-051 added and routed as a gap, the due date and the page limit. All six must
  pass.
- **`ocr_surfacing`** (RFP D threshold **1.00**): every requirement whose text differs from the key
  (case, spacing, quotes and trailing punctuation ignored), and every requirement with an ID the key
  lacks, must carry an `OCR-LOW-CONFIDENCE` flag. Requirements the addendum revised or added are left to
  `addendum_assertions`. The key's text is read only here, inside `eval/`.
- **Design-only flag types** (`OCR-LOW-CONFIDENCE`, `LIMIT-BREACH`, listed in `thresholds.json`) are left
  out of flag precision. The key doesn't list them, so counting them as spurious measured the design, not
  the run.
- **RFP D runs in two phases** (`eval/run.ps1 -Rfp D`): the scanned base runs to stage 9 with OCR
  verifications confirmed and gaps closed by script, its approval card is cancelled, Addendum No. 1 is
  uploaded, and the reprocessed run is completed the same way before scoring. Verifications are only
  ever confirmed, never corrected (ADR-0025).
- Headline thresholds for D and the 2-of-3 pass rule don't change.

**Adjudications (RFP D).** Approved under the user's "approve everything" in the Slice 5 session, and listed
in the slice summary for review:
- **THPA-038** (dedicated single-tenant database). The key routes the gap to Availability, Backup & DR; the
  pipeline classifies it Architecture & Hosting, whose description covers the hosting model.
- **THPA-029** (operate with no internet connectivity). The key has a gap with no rule; the pipeline reports
  No under R-09 (customer-hosted deployment, Block), since a SaaS-only company can meet it only by a
  customer-hosted deployment. Tunings 7–9 left that reading unchanged. The user confirmed this on review, 2026-11-01.

**Tuning (Slice 5).** By failure category, with no example taken from RFP D:
- **Tuning 7:** selection treats activities, deliverables and arrangements as particular kinds; relations
  treat an undefined whole as ambiguous and a stated or committed value as not.
- **Tuning 8:** relations check that dates and durations add up; adjudication doesn't fire on an inferred
  consequence; selection's falls-short rule is about degree, not a different arrangement.
- **Tuning 9:** answers covering only part of the requirement don't answer it; a condition naming a
  deployment or arrangement fires only when the requirement names it.

**Defects found by the exit runs, fixed before the runs that count:**
- `Clear_Relations` in stage 3 read the wrong ID column.
- A prompt's structured output dropped by AI Builder (an extra property) left a null; flows now parse the
  text.
- The interpret prompt wrote item labels with brackets (`[B.1]`) and validation rejected them; labels are
  normalized in code.
- Redeploying the pipeline flow while a run waited lost one resume trigger. Deploy flows only between runs.

**Result.** On the fixed pipeline, full two-phase runs:
- **Run 4** (`eval/reports/20261031-172801-D-dev.md`): PASS.
- **Run 5** (`20261031-180156`): PASS.
- **Run 6** (`20261031-181904`): PASS.
- All three runs: extraction 50/50, compliance 47/48, gap routing 5/5, clarifications 2/2, all 6 addendum
  assertions, must-not-use 0. Every OCR text matched the key, so `ocr_surfacing` had nothing to score
  (all 8 low-confidence requirements were flagged and confirmed).
- **RFP D passes 3 of 3**, so it meets the 2-of-3 rule. Earlier runs (`153020`, `171220`) hit the defects above and are kept as
  the record.
- **Regression:** RFP A (`20261031-173625`) and RFP B (`20261031-174614`) both PASS with tunings 7–9.
