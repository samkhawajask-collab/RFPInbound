# ADR-0021 — Self-consistency for the relation and selection prompts

- **Status:** Accepted
- **Date:** 2026-10-29
- **Deciders:** Solution architecture, with the user's approval in the Slice 2 session
- **Related:** [ADR-0009](0009-evaluation-harness-outside-the-platform.md), [ADR-0015](0015-leaf-steps-are-prompts-called-by-agent-flows.md), [ADR-0019](0019-meaning-based-candidate-recall.md)

## Context

After tuning, RFP A passed every threshold in one run (`eval/reports/20261029-201421-A-dev.md`). An
unchanged repeat (`20261029-201916-A-dev.md`) failed gap routing 5/7, from variance in two judgment
prompts. The prompts run at temperature 0, and the model still doesn't return identical output every time:
- **RFP Judge Relations:** CV-011 was judged out of scope, and CV-054 and CV-057 were put in a
  contradiction.
- **RFP Select Answers:** an answer was picked for CV-031, which the key treats as a gap.

The thresholds that depend on these prompts have small denominators: 7 gaps, 1–2 flags per relation
type. One flipped judgment fails them.

## Options considered

- **A — Accept the variance and define a pass rule only.** Cheap, but it leaves the pipeline's output
  as unstable as the score.
- **B — Self-consistency: run each of the two prompts twice and keep only what both passes agree on**
  *(chosen)*.
- **C — Majority of three passes.** More stable, but three times the cost of these prompts. Agreement
  between two already removes one-off judgments.

## Decision

**Option B**, applied asymmetrically. A disagreement resolves to the cautious outcome.

- **Relations (stage 4).** `agree_relations` keeps a duplicate pair, an ambiguous or out-of-scope
  finding, or a contradiction-group member only when both passes report it. A finding only one pass makes
  is dropped. Missing a relation means a requirement is answered normally, and a person reviews the
  proposal anyway. A false relation changes compliance (No or Clarify) or removes a requirement from
  answering.
- **Selection (stage 5).** `agree_selections` keeps an answer only when both passes find one. If one
  pass finds no candidate on the subject, the requirement goes on unanswered, and stage 6 decides whether
  it is a gap. Gaps go to an SME, which is safer than a weakly supported answer in a proposal. If both
  passes find an answer but different ones, the first stands: both are validated Approved candidates on
  the subject.
- **The two passes run in parallel**, so stage time is unchanged. Cost: one extra call for each of the two
  prompts per run.
- **Pass rule (ADR-0009, amendment 2):** a format passes when its thresholds are met in **2 of 3
  consecutive** scored runs with no changes between them, and must-not-use is 0 in **all** of them.

## Consequences

- Recall of relation flags can fall: a duplicate one pass misses is now dropped. The scored runs show
  whether that costs a judgment-flag threshold.
- Unanswered requirements can rise slightly. That's the intended direction: an SME looks at a gap, while
  a weak answer might go out unchallenged.
- Other judgment prompts (rule adjudication, the shortlist and screen prompts) stay single-pass. The
  shortlist and the screen only add candidates, and the decision prompts after them filter. Adjudication
  can be added to this scheme if its variance shows up in scores.

## Amendment 1 — 2026-10-30: relations take a majority of three passes

**Context.** Under the pass rule (ADR-0009 amendment 2), RFP A met its thresholds in 1 of 3 runs
(`eval/reports/20261030-072726-A-dev.md` PASS, `20261030-075105-A-dev.md` and `20261030-111706-A-dev.md`
FAIL). Each failure was one relation finding missing: an ambiguous requirement in one run, a duplicate in
the other. Relation flag precision was 1.000 in all three runs. Requiring both of two passes to agree
drops any finding that either pass misses, so with only 1–2 cases per flag type, one missed judgment fails
the threshold. The consequence this ADR predicted ("recall of relation flags can
fall") showed up in the scores.

**Options.** (1) Majority of three passes for relations *(chosen, approved by the user in the Slice 2
session)*. (2) Union of two passes with a deterministic guard, such as text overlap for duplicates. A text
guard blocks paraphrased duplicates, which the prompt is told to find, and has no equivalent for
ambiguity. (3) Leave the rule alone and change the thresholds for flag types with few cases (an ADR-0009
contract change).

**Decision.** RFP Judge Relations runs **three** times in parallel. `agree_relations` keeps a duplicate
pair, an ambiguous or out-of-scope finding, or a contradiction member when **at least two** passes report
it. The reason comes from the first pass that reports the finding. A contradiction group is taken from the
first pass that has the majority members. With two passes the rule is unchanged (both must agree).
Selection (stage 5) stays at two passes and keeps its cautious rule.

**Observed variation (RFP D, Slice 7 code, 2026-11-01).** Compliance across five runs: 0.979, 0.917 (FAIL), 1.000,
0.979, 1.000, against a threshold of 0.92. Every miss is Full reported as Clarify, and each lines up with a spurious
AMBIGUOUS or CONTRADICTION flag in that run, because compliance is computed from those flags (`compliance.py`). So
false relation findings still pass the majority rule at times. With 48 requirements, four such misses fail the
threshold and three don't. The failed run overlapped an RFP A run; whether that contributed is unknown. Must-not-use
violations stayed at zero, and Clarify asks the buyer rather than giving a wrong answer, so this fails cautiously.
**Decision:** no prompt change. Tuning against these requirements would fit the answer key, and 1 failure in 5 runs is
too few to act on. Keep watching the rate.

**Consequences.** One finding missed by one pass no longer drops it, and one-off findings by a single pass
are still dropped. False findings rise slightly: a finding two of three passes make is kept, where before
both of two had to. Precision thresholds will show whether that matters. Cost: one more relations call
per RFP (stage 4 makes one call for the whole requirement set), and stage time is unchanged because the
passes run in parallel.
