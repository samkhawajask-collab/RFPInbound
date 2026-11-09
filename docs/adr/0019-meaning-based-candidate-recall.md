# ADR-0019 — Meaning-based candidate recall for answers and policy rules

- **Status:** Accepted
- **Date:** 2026-10-29
- **Deciders:** Solution architecture
- **Related:** [ADR-0004](0004-answer-retrieval-filtered-candidate-set-not-knowledge-rag.md) (amended), [ADR-0005](0005-policy-engine-two-stage-deterministic-then-adjudicated.md) (amended), [ADR-0009](0009-evaluation-harness-outside-the-platform.md)

## Context

The first scored run of RFP A (Slice 2, `eval/reports/20261029-184935-A-dev.md`) cited a correct answer every
time it cited one, but cited none for 11 requirements that have an approved answer. `rfp_eval.diagnose`
split the misses by layer:

- **5, category filter.** The requirement was classified into a different category from its answer, and
  candidates were drawn from the requirement's category only.
- **3, word-overlap ranking.** The answer was in the category but scored low or zero, because the RFP and
  the library describe the same subject in different words ("maximum acceptable data loss" against
  "recovery point objective"; "servers in the Authority's data center" against "hosting model").
- **3, prompt or design.** Covered by other changes.

The policy engine had the same weakness. Two Block rules did not fire because their trigger phrases
("agency data center", "supply of hardware") never appear in requirements that plainly meet the rule's
condition. ADR-0005's first stage is high-recall phrase matching, and on paraphrase it isn't.

## Options considered

- **A — More synonyms and abbreviations in code.** Cheap, but each list entry is a guess, and tuning the
  list from scored misses drifts towards encoding the answer key.
- **B — Embeddings (Azure OpenAI) with vector similarity.** Good recall, but it adds a service, a key and an
  index to keep in step with the library. That's new infrastructure for 198 answers and 35 rules.
- **C — A screening prompt over the register's short form** *(chosen)*.
  - For answers: the model sees the **questions of Approved answers** (ID, category, question).
  - For rules: the model sees **the rule register** (ID, severity, condition).
  - It returns candidate IDs per requirement. Code validates the IDs and unions them with the
    deterministic matches.

## Decision

**Option C, as a recall step in front of the existing decision steps.** It is never the decision itself.

- **Answers (stage 5).**
  - `RFP Shortlist Answers` sees only Approved answers' IDs, categories and questions. It never sees answer
    text, or anything Retired or Draft (ADR-0004 holds).
  - It returns up to 3 IDs per requirement, in any category.
  - `selection_items` keeps only IDs of Approved answers. It puts them first, then adds the word-overlap
    shortlist in the requirement's category, up to 8 candidates.
  - `RFP Select Answers` still chooses one candidate or none.
- **Rules (stage 6).**
  - `RFP Screen Rules` sees rule IDs, severities and conditions.
  - It returns candidate rule IDs per requirement. `adjudication_items` keeps only known IDs and unions
    them with the trigger-phrase and threshold matches.
  - `RFP Adjudicate Rules` still decides whether each rule fires, and numeric thresholds are still
    compared in code (§8.2).
- **Cost:** two more prompt calls per run (one per stage), each about 5–8k tokens for RFP A.

## Consequences

- **Category becomes a ranking hint, not a filter, for answer retrieval.** A misclassified requirement can
  still reach its answer.
- **The deterministic layers are unchanged and still run.** The screen can only add candidates. A screen
  outage degrades recall back to the Slice 1 level; it never produces a wrong answer by itself.
- **Hard rule 5 is unaffected.** Retired and Draft rows are excluded before the shortlist prompt sees the
  library, and selected IDs are validated against the Approved set in code.
- **The library questions and rule conditions now reach a model.** They are company data that the
  selection and adjudication prompts already saw in part. No new data class is exposed.

## Amendment 1 — 2026-10-30: tried and reverted, prompt rules for things the company does not offer

**Context.** After tuning 7, two answer misses on RFP A were stable across every run. Both requirements
ask for something the company does not offer: equipment it does not supply, and a deployment model it
does not provide. The stage 5 prompt outputs showed the meaning-based shortlist returning no candidates
for either one, though the library has questions on both subjects. In one case, the selection prompt
rejected its only candidate because none "supports" what the requirement asks for.

**Tried (tuning 8).** One rule in each prompt, with examples on unrelated subjects:
- The shortlist treats a requirement for something the company may not offer as on the topic of library
  questions about that subject.
- Selection chooses a candidate that says the company does not offer what is asked.

**Result** (`eval/reports/20261030-132811-A-dev.md`, one run):
- Neither target was fixed. One requirement's shortlist found a neighboring question (where data is
  hosted) instead of the position statement, and selection still rejected it because nothing
  "supports" the demand. The other requirement's shortlist stayed empty.
- The looser shortlist also matched retired answers on merely related subjects, for example years in
  operation against headcount. That added a spurious LIBRARY-RETIRED-MATCH flag.

**Decision.** Reverted. The prompts are back to their tuning 7 text (republished in dev as
configuration 5). Pushing harder toward these cases risks fitting the key. The equipment-supply
requirement is adjudicated instead (ADR-0009 amendment 3, `eval/adjudications.json`). The deployment
model requirement stays a known miss within thresholds.

## Amendment 2 — 2026-10-30: conflicting approved answers use the shortlist's topic judgment

**Context.** LIBRARY-CONFLICT was raised only when two approved answers in the same category had
questions with enough shared words (`topic_similarity` of at least 0.5). `library.py` already recorded
the known gap: pairs phrased with no shared words, such as data return or implementation duration, need
semantic judgment (assumption A6). RFP B's tuning 2 run (`eval/reports/20261030-154843-B-dev.md`) missed
exactly those conflicts, for example "What is a typical implementation timeline?" (6–9 months) against
"How long does implementation take?" (9–12 months).

**Decision.** `annotate_answer` also treats as a conflict any approved answer that the meaning-based
shortlist listed for the requirement and that states different facts from the selected answer
(`facts_conflict`, unchanged). This is the same use of the shortlist that Retired and Draft matches
already make. A long-form answer that repeats and extends the selected one still doesn't conflict.

**Consequences.** Conflict recall no longer depends on wording. If the shortlist lists answers on merely
related subjects that state different figures, conflict precision can fall, and the scored runs show
whether it does.
