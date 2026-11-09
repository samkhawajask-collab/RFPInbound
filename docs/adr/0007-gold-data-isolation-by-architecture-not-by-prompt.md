# ADR-0007 — Gold data isolation by architecture, not by prompt

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0009](0009-evaluation-harness-outside-the-platform.md)

## Context

Hard rule 1: `data/.../08_Gold_Data/` and `09_Dataverse_Import/90_eval_goldrequirement.csv` are for
scoring only. They must never reach an agent knowledge source, a Dataverse table the agents can
read, a prompt, or a tool the agents can call. Only code under `eval/` may read them.

The data set's own README repeats this: *"Keep 08_Gold_Data and 90_eval_goldrequirement.csv out of
the agent's knowledge sources; they are for scoring only."*

The stakes are specific. The gold data contains, per requirement, the expected answer ID, the
acceptable alternates, the **must-not-use IDs**, the expected compliance value, the triggered rules,
the duplicate and contradiction markers, the expected SME and the expected clarification text. An
agent with any access to it would score near-perfectly while demonstrating nothing. The leak would
not announce itself — it would look like success.

`09_Dataverse_Import/Dataverse_Schema.md` also offers `hrs_evalgoldrequirement` as an optional
table, with the note "For an evaluation environment only; never expose to the production agent".
The temptation to load it for convenience is real.

## Options considered

### Option A — Instruct the agents not to use it

A sentence in each agent's instructions.

- **For:** Zero effort.
- **Against:** Not a control. An instruction cannot prevent access that exists; it only expresses an
  intention. For the single most important constraint in the project, this is the weakest possible
  mechanism.

### Option B — A pre-tool-use hook that denies calls touching gold paths

Use the hook's `permissionDecision = deny`.

- **For:** A real runtime check at the point of use, scoped to specific tools.
- **Against:** Microsoft's own documentation says hooks fail open: *"Hooks don't stop the agent when
  they fail… the agent continues as though the hook returned nothing. Don't rely on a hook as your
  only safeguard for a business-critical rule."* Hooks are also preview and harness-specific. Useful
  as one layer, unacceptable as the layer.

### Option C — Structural isolation in four layers, with a CI check *(chosen)*

Make the access impossible rather than forbidden.

- **For:** Each layer holds independently. A later well-meaning change — someone attaching a
  SharePoint site as a knowledge source, someone importing the eval table "to test something" — is
  caught by a different layer than the one it bypassed.
- **Against:** The evaluation harness needs its own environment access path, and the CI check needs
  maintaining as the repo grows.

## Decision

**Option C.** Four layers:

**1. Residence.** `08_Gold_Data/`, `09_Dataverse_Import/90_eval_goldrequirement.csv` and
`10_Generator_Source/gen_gold.py` live outside any location an agent can reach. They are not in the
company content library, not in the templates library, and not in any SharePoint site registered as
a knowledge source. The inbound RFP library contains only the four source documents and the
addendum.

`gen_gold.py` is included deliberately. It *computes* the answer key — expected answers,
must-not-use IDs, expected compliance, the flag set — so it is answer-key material even though it
sits in the generator folder. (This is open question Q4; the CI check treats it as gold unless the
decision is reversed.)

**2. Schema.** `hrs_evalgoldrequirement`, `hrs_evalrun` and `hrs_evalresult` are **not components of
the production solution**. They are created only in the evaluation environment, by a separate
script. A production import cannot bring them along because they are not in
`solutioncomponents`.

**3. Permission.** The agents' service identity holds no security-role privilege on those tables.
In the evaluation environment, where the tables do exist, the agent identity still cannot read them.
This is the layer that holds if layers 1 and 2 are undone by a future change, because a
mis-attached knowledge source over a table the identity cannot read returns nothing.

**4. Code and CI.** Only code under `eval/` opens those files. A CI check fails the build when any
path outside `eval/` references `08_Gold_Data`, `90_eval_goldrequirement` or `gen_gold`. The check
runs on every commit, not only on release.

**Hooks are added on top**, not instead: a pre-tool-use hook denies tool calls whose parameters
reference a gold path. It is layer five, and nothing depends on it.

## Consequences

- The evaluation harness cannot run inside the Power Platform environment that hosts the agents. It
  runs outside, reads run output through the Dataverse Web API, and reads the answer key from the
  file system. That constraint is the reason for
  [ADR-0009](0009-evaluation-harness-outside-the-platform.md).
- Copilot Studio's built-in Evaluate feature cannot be used for scoring against gold, because it
  would need the answer key inside the agents' own environment.
- Two environments are required even for a pilot: one where the agents run, one where scoring
  happens.
- The CI check is a build-breaking test, so a legitimate future need to read gold data from new
  code requires moving that code under `eval/` or amending this ADR — deliberately, in review.
- Any report, document or prompt that quotes a gold value is a violation. Expected counts quoted in
  `ARCHITECTURE.md` and in these ADRs are *aggregate* counts used to validate derivation logic, and
  live in documentation that agents do not read; they must never be copied into an agent
  instruction, a knowledge source or a test fixture the agents can reach.
