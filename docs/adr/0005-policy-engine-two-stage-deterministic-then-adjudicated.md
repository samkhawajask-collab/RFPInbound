# ADR-0005 — Policy engine: deterministic match, then adjudication

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0006](0006-compliance-value-and-approval-level-are-code.md)

## Context

The policy rule register holds 35 rules. Each has a trigger pattern (a semicolon-separated phrase
list), a written condition, a severity (Block / Warn / Info), an approved position and an escalation
owner.

Two properties of the data set drive the design.

**Precision matters more than recall.** Across 311 requirements the gold data expects only **24
policy-rule flags** — 5 to 8 per RFP. Phrase matching alone would fire far more often. Rule R-06's
trigger is literally `certification required; certified to`, which appears in many requirements that
do not demand a certificate the company lacks. A flood of false exceptions in the proposal's
Exceptions section destroys the approver's trust in the whole output, and 24 true flags buried in
200 false ones is worse than nothing.

**A large minority of the rules are arithmetic.** Fourteen of the 35 carry a numeric threshold in
their written condition: availability above 99.9%, payment terms longer than net 60, penalties above
10% of annual value, cyber insurance above $10M, RPO under 1 hour, RTO under 4 hours, breach
notification under 72 hours, P1 response under 1 hour, termination notice under 90 days, price hold
beyond 3 years, accessibility above WCAG 2.1 AA, performance bond above 10%, non-solicitation beyond
12 months, and a go-live date that compresses the standard 6-9 month timeline. Whether 99.95%
exceeds 99.9% is not a judgment call.

The addendum makes the point sharply: THPA-036 changes from 99.5% to 99.95%, crossing the R-02
threshold and flipping the requirement from Full to No.

## Options considered

### Option A — Pure phrase matching

Fire a rule when any trigger phrase matches.

- **For:** Trivial, fast, free, perfectly reproducible.
- **Against:** Precision collapses. The trigger is a keyword; the condition is the actual test. R-06
  would fire on every mention of a certification. Numeric rules would fire regardless of whether the
  buyer's number actually breaches the threshold.

### Option B — Give the agent the whole register and let it decide

Attach the 35 rules and ask the model which apply.

- **For:** Simple to build; handles paraphrase naturally.
- **Against:** 35 rules × 311 requirements is a large, repeated judgment with no determinism, and
  the arithmetic is done by a language model. Severity, which drives compliance and the approval
  level, would be model output. Reproducibility across runs is lost, which breaks the evaluation.

### Option C — Deterministic candidate match, agent adjudication, deterministic mapping *(chosen)*

Three stages: code for recall, the model for reading the condition, code for consequences.

- **For:** The model is used for the one thing it is better at — deciding whether a written
  condition is actually met — and for nothing else. Arithmetic stays arithmetic. Severity,
  compliance and approval level stay code. Adjudication runs only on candidates, so cost is bounded
  by candidate count, not by 35 × 311.
- **Against:** Three moving parts. Recall now depends on the quality of phrase normalization and
  typed-value extraction: a rule whose trigger never matches is never adjudicated and is silently
  missed.

## Decision

**Option C.**

**Stage 1 — candidate match (code, `rfp-tools`).**
- `match_rule_candidates` matches `hrs_triggerpattern` phrases with case, punctuation and whitespace
  normalized, plus common morphological variants. Tuned for recall; false positives are expected and
  acceptable at this stage.
- `evaluate_thresholds` extracts typed values from the requirement text — percent, hours, days,
  currency, net terms, WCAG level, bond percentage — and performs the comparison against the rule's
  threshold. The model never does the arithmetic.
- `check_certifications` recomputes certificate validity against the **submission date**, not
  today's date. `hrs_certification.hrs_statusasof20261027` is treated as an informational snapshot,
  exactly as the import schema instructs.

**Stage 2 — adjudication (`RFP Policy Adjudication` agent).**
For each candidate, the agent receives the requirement text, the rule's trigger, condition and
approved position, the extracted numeric values and the current certification status. It returns
fired / not fired and a one-line reason, which is stored on the flag so a human can see why.

**Stage 3 — mapping (code).**
Severity comes from `hrs_severity`. Compliance comes from the precedence table in
[ADR-0006](0006-compliance-value-and-approval-level-are-code.md). SME routing and approval level
follow the documented rules. None of these is model output.

**Reasoning model.** Adjudication is one of only two steps where a reasoning-capable model is
enabled, because an error here moves a compliance value. Note that a reasoning model adds the
premium rate (10 credits per 1K tokens) on top of the feature rate.

**Flags.** A fired rule writes a `POLICY-RULE` flag with the rule ID, severity, the condition and
the approved position, matching the register's own wording. An expired certificate additionally
writes `CERTIFICATION-INVALID` naming the certificate and its expiry date.

## Consequences

- Flag **precision** is a first-class scored metric with its own threshold (0.85), not just recall.
  A build that finds all 24 true flags plus 100 false ones fails.
- `POLICY-RULE` recall threshold is 0.90, while `CERTIFICATION-INVALID` is 1.00 — the latter is pure
  date arithmetic and any miss is a defect.
- Adjudication cost scales with candidate count. If stage 1 is over-eager, cost rises before
  precision does, so candidate counts are logged per run and reviewed.
- Rules whose trigger phrase never matches are invisible. Slice 1 therefore checks per-rule
  candidate coverage against the known fired rules per RFP (A: R-01, R-02, R-06, R-09, R-29;
  B: R-04, R-05, R-14, R-20, R-26; C: R-03, R-07, R-10, R-12, R-26, R-27, R-29, R-34;
  D: R-02, R-26, R-29, R-30, R-33, R-34) as a recall smoke test on stage 1 alone.
- Every Block and Warn flag must appear in proposal section 9, Exceptions — enforced as gate G5.
- Info-severity rules are carried into the response text as the approved position and do not change
  compliance.

## Amendment — 2026-10-29 (Slice 2, ADR-0019)

The first scored run confirmed the risk above. Two Block rules (R-09, R-29) never became candidates,
because RFP A paraphrases their conditions without any trigger phrase. Stage 1 now has a second,
meaning-based input: `RFP Screen Rules` reads the rule register (ID, severity, condition) and proposes
candidate rules per requirement. Code validates the rule IDs and unions them with the trigger-phrase and
threshold matches. Adjudication (stage 2) still decides every candidate, and threshold comparisons stay
in code. The screen can only add candidates.
