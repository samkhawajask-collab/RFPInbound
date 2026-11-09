# ADR-0004 — Answer retrieval is a filtered candidate set, not knowledge RAG

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0001](0001-deterministic-workflow-spine-agents-at-the-leaves.md), [ADR-0007](0007-gold-data-isolation-by-architecture-not-by-prompt.md)

## Context

Hard rule 5: logic that selects library answers must exclude `Retired` and `Draft` entries, flag
answers last reviewed more than 18 months ago, flag topics with conflicting approved answers, and
every drafted response must carry the answer ID it came from.

The answer library is 212 entries: 198 approved, 8 retired, 6 draft, with 24 approved entries stale.
It is deliberately adversarial to similarity search:

- **8 retired answers whose question text is identical to the current entry's** — IE11 browser
  support, a 99.5% SLA, SOAP API, ISO 27001:2013. A similarity ranker cannot tell them apart,
  because by construction they are equally similar.
- **5 conflicting approved pairs** — RPO 1 hour vs 4 hours, support hours, pen-test frequency, data
  return timing, implementation duration. Both are approved; only one agrees with the company
  content.
- **6 drafts**, three of which overpromise on covered topics and three of which look like answers
  for gap requirements. A gap must stay a gap.

Across the four RFPs the gold data expects 21 retired-match flags and 13 conflict flags, and **zero
must-not-use violations** is the one threshold with no tolerance.

## Options considered

### Option A — Answer library as a Copilot Studio knowledge source

Attach the library (as Dataverse or as files) and let the drafting agent retrieve from it.

- **For:** Least code; the platform handles chunking, embedding and ranking; easy to extend.
- **Against:** Fatal for this dataset. Retrieval ranks by similarity, and the retired traps are
  *constructed* to be maximally similar. Status filtering would have to happen after retrieval, as
  an instruction — which is precisely the "prompt instead of enforce" failure the hard rules exist
  to prevent. There is no reliable way to guarantee a retired answer is never surfaced, and
  "guaranteed" is the requirement.

### Option B — Deterministic lookup only, by canonical topic

Map each requirement to a canonical topic and look up the single approved answer.

- **For:** Perfectly reliable where the mapping exists.
- **Against:** Requires a maintained topic map. The gold data contains exactly such a map
  (`gold_library_topic_map.csv`) and explicitly says not to expose it to the agent — using it would
  be reading the answer key. Building our own equivalent is real work and brittle for requirements
  the taxonomy has not anticipated.

### Option C — Filtered query, then semantic rerank within the filtered set, then one answer to the agent *(chosen)*

The flow queries Dataverse with a hard filter, reranks only what survives, annotates the chosen
candidate with the four library issues, and hands the drafting agent exactly one answer row.

- **For:** Status filtering happens in the query, so retired and draft entries are *never fetched* —
  the guarantee is structural. Staleness and conflict are computed, so those flags are deterministic
  and should score 1.00. The agent cannot cite an answer it was not given. The required lookup on
  `hrs_response.hrs_answer` makes an uncited response unsaveable.
- **Against:** More code than Option A. Rerank quality inside the filtered set is now our problem.
  Conflict detection needs a notion of "same topic" that we must derive, since the gold topic map is
  off-limits (open question A6).

## Decision

**Option C.** The chain, executed by `RFP - Select Answers`:

1. **Filter** — `hrs_answer` where `hrs_approvalstatus = Approved` **and** `hrs_categorycode` equals
   the requirement's category. Retired and draft entries are excluded before the model is involved.
2. **Rerank** — semantic similarity against `hrs_question`, within the filtered set only.
3. **No candidate** → raise `NO-LIBRARY-ANSWER`, create `hrs_gap`, set compliance `Gap`, and write
   **no draft at all**. A gap is never papered over with a plausible sentence.
4. **Annotate** the selected candidate:
   - `hrs_lastreviewed` < submission date − 18 months → `LIBRARY-STALE`, set
     `hrs_response.hrs_smereviewrequired`;
   - another approved answer on the same topic stating different facts → `LIBRARY-CONFLICT`,
     require owner resolution;
   - a retired answer whose question text matches → `LIBRARY-RETIRED-MATCH` (informational only —
     the retired text is never passed on);
   - a draft answer exists on the topic → `LIBRARY-DRAFT-PRESENT` (informational only).
5. **Draft** — `RFP Drafting` receives exactly one `hrs_answer` row plus the company content its
   category permits, the word limit and the counting rule, and any flags already on the requirement.
6. **Save** — `hrs_response` with `hrs_answer` as a **business required** lookup.

The answer library is **not** attached as a knowledge source to any agent. Past proposals are **not**
attached either: their bodies contain since-retired statements by design, and only their metadata is
read, as evidence of prior use.

Library issues **never** change the compliance value. They change which answer may be used and
whether SME review is required. (See [ADR-0006](0006-compliance-value-and-approval-level-are-code.md).)

## Consequences

- `must_not_use_violations = 0` is achievable by construction rather than by tuning, because the
  violating rows are never read.
- `LIBRARY-STALE`, `LIBRARY-RETIRED-MATCH` and `LIBRARY-DRAFT-PRESENT` are deterministic and carry a
  1.00 recall threshold. A score below 1.00 on any of them is a bug, not a quality problem.
- `LIBRARY-CONFLICT` depends on deriving a canonical topic without the gold map. Slice 1 measures
  its recall; if the derived grouping is too noisy, the fix is a stored canonical-topic column on
  `hrs_answer`, maintained by owners — not loosening the threshold.
- Rerank quality inside the filtered set drives the answer-ID accuracy metric (threshold 0.90).
- SME answers to gaps are proposed as `Draft` library entries owned by the relevant SME; promotion
  to `Approved` stays a human act, so the pipeline cannot grow the approved library by itself.
- `hrs_confidentiality = NDA-only` answers need an NDA status per opportunity, which the pilot data
  does not have (open question Q7).

## Amendment — 2026-10-29 (Slice 2, ADR-0019)

The first scored run showed the category filter and word-overlap ranking were the weak points, not the
rerank. 8 of 11 missed answers never reached the shortlist. Three changes:

- **Meaning-based shortlist.** `RFP Shortlist Answers` adds candidates by meaning, in any category.
  Category becomes a ranking hint, not a filter.
- **Retired and Draft questions reach a model, on a flag-only channel.** The shortlist prompt sees their
  questions, never their answer text, in a list marked unusable. It returns them only as `unusableIds`,
  which code validates against the Retired/Draft rows and uses to raise `LIBRARY-RETIRED-MATCH` and
  `LIBRARY-DRAFT-PRESENT`. They can never become candidates: selection still only accepts Approved IDs,
  validated in code.
- **These two flags are no longer purely deterministic.** They combine the superseded-by link and code
  similarity with the shortlist's judgment. Their 1.00 recall threshold stands: word overlap alone could
  not tell a true match from a coincidence across categories.

The consequence above that "the violating rows are never read" now holds for the *answer text* of
violating rows, which is what matters for must-not-use.
