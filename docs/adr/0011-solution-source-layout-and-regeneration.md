# ADR-0011 — Solution source layout and regeneration

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture
- **Related:** [ADR-0001](0001-deterministic-workflow-spine-agents-at-the-leaves.md), [ADR-0012](0012-harness-selection-github-copilot-harness.md)

## Context

Hard rule 2: solution changes are made only in `solution/src/`, never by hand-editing a packed
`.zip`, and the solution is re-packed after every change. Claude Code generates and regenerates that
source, so the layout has to be one a generator can emit deterministically and one that
`pac solution pack` will accept.

Three layouts exist, and they are not interchangeable.

**1. Classic XML exploded** — what `pac solution unpack` produces by default, and what the Copilot
Studio gallery's own submissions use. Reading the gallery's `blastbox-omega` submission directly
(four agents, two workflows, four MCP servers, Python skills, a custom connector) gives:

```
solution/
├── solution.xml
├── customizations.xml
├── [Content_Types].xml
├── bots/
│   └── <prefix>_<agentschemaname>_<suffix>/
├── botcomponents/
│   ├── <botschema>.tool.connected-agent.<AgentSchemaName>/
│   ├── <botschema>.skill.<skill-name>/
│   └── <botschema>.file.<filename>/
├── Workflows/
└── Connector/
```

That naming is informative beyond layout: a **connected agent is itself a bot component** of the
parent agent (`…tool.connected-agent.<AgentSchemaName>`), as are skills and files. Adding a connected
agent is a solution change, not a configuration change.

**2. YAML source control format** — produced by `pac solution clone` / `pac solution sync` and by
native Dataverse Git integration: `solutions/<name>/{solution.yml, solutioncomponents.yml,
rootcomponents.yml, missingdependencies.yml}`, `publishers/<name>/publisher.yml`, then `entities/`,
`workflows/`, `modernflows/`, `canvasapps/`, `environmentvariabledefinitions/`, `connectors/`.
Requires `pac` ≥ 2.4.1. Modern flows and canvas apps are supported **only** in this format.

**3. Copilot Studio VS Code extension layout** — agent-centric, not solution-centric:
`agent.mcs.yaml`, `settings.mcs.yml`, `connectionreferences.mcs.yml`, plus `actions/`, `topics/`,
`knowledge/files`, `workflows/`, `trigger/`, `icon.png`. Documented for standard-harness agents, and
it carries one agent, not a solution.

## Options considered

### Option A — YAML source control format

- **For:** Reviewable YAML diffs; the canonical format for Dataverse Git integration; the only
  format that round-trips modern cloud flows and canvas apps. `solutioncomponents.yml` makes
  membership explicit.
- **Against:** Requires `pac` ≥ 2.4.1 on every machine and in CI. Documented pitfalls bite exactly
  the generator case: a component declared in `rootcomponents.yml` whose source file is missing
  packs **successfully** with only a warning and is silently dropped from the `.zip`, and an
  incomplete `solutioncomponents.yml` packs clean but fails on import. A generator that produces
  these files is the most likely thing to produce that failure, and the failure is quiet.

### Option B — VS Code extension layout

- **For:** Best per-agent authoring experience; `agent.mcs.yaml` is the most readable
  representation of an agent.
- **Against:** Not a solution. It holds one agent and has no place for tables, connectors, cloud
  flows or environment variables. Its own documentation points to solutions for multi-environment
  deployment.

### Option C — Classic XML exploded in `solution/src/` *(chosen)*

- **For:** It is what `pac solution unpack` emits by default, so portal-drift reconciliation is a
  single command with no format conversion. It is the convention the gallery uses, which matters
  because the generator is meant to emit something recognizable to that ecosystem. No `pac` version
  floor. Fewer silent-drop failure modes than hand-generated YAML manifests.
- **Against:** XML diffs are noisier than YAML. Canvas apps and modern flows are not supported in
  this format — acceptable here, since there are no canvas apps, but it constrains flow
  representation. **[UNVERIFIED]** whether the agent flows used by the pipeline serialize into
  `Workflows/` in the XML format or require the YAML format's `modernflows/`. This is assumption A2
  and is tested in slice 0.

## Decision

**Option C**, with a planned verification.

- `solution/src/` holds the classic XML exploded layout shown above, plus `Entities/`,
  `environmentvariabledefinitions/` and `connectionreferences/`.
- `solution/build/` holds the packed `.zip` and is git-ignored.
- Reconciliation of any portal change is `export` then `pac solution unpack` into `solution/src/`,
  in the same working session, so git stays the source of truth.
- **Verified 2026-10-28 (Slice 1 spike, pac 2.14.1):** agent flows (`category 5`, `modernflowtype 1`)
  unpack to `Workflows/<Name>-<GUID>.json` + `.data.xml`, and AI Builder prompts unpack into
  `Other/Customizations.xml` under `<AIModels>`. Both repack and re-import cleanly. Learn's
  "modern flows: YAML format only" row describes the YAML layout's `modernflows/` folder; it doesn't
  stop classic unpack from round-tripping flows. The classic XML layout stands. See
  [ADR-0015](0015-leaf-steps-are-prompts-called-by-agent-flows.md).
- **How tables get into `solution/src/`:** generated from `solution/spec/tables.json`, applied to dev,
  then exported and unpacked. See [ADR-0014](0014-dataverse-schema-from-spec.md).
- **Layout verification (moved to the start of Slice 1, before any agent or flow is generated):**
  unpack a trivial two-agent solution that contains one connected agent,
  one agent flow, one cloud flow, one table and the custom connector, and diff the result against
  this layout. If agent flows do not round-trip in the XML format, this ADR is superseded by a move
  to the YAML format and the `pac` ≥ 2.4.1 floor is adopted.

### Generation rules

1. **Source is authoritative.** Generators write `solution/src/`; nothing edits a packed `.zip`.
2. **Idempotence.** Byte-stable output for unchanged input, so a regeneration produces an empty
   diff. Where the platform assigns GUIDs or name suffixes (`<prefix>_<agent>_<suffix>`), the
   generator reads them from existing source rather than minting new ones — otherwise every
   regeneration would recreate every component.
3. **Explicit solution membership.** Every component is attached to the named solution on write, so
   nothing orphans into the default solution.
4. **No environment-specific literals.** Environment variables and connection references only. No
   URLs, GUIDs, keys or tenant IDs in committed source (hard rule 4).
5. **Pack, check, then import.** `pac solution pack` → `pac solution check` → import to dev or test
   only. A check failure blocks the import.

### Branching and release

- `main` is always importable into dev.
- One branch per slice (`slice/rfp-a`, `slice/eval-harness`, …), merged only when that slice's
  definition of done is met.
- Release: export managed from dev, import to test, run the evaluation, tag with the solution
  version and the evaluation scores.

## Consequences

- Portal-to-git reconciliation is one command, which makes the "reconcile immediately" rule
  realistic rather than aspirational.
- XML diffs are noisy, so review attention goes to the generator's input specification rather than
  to the generated XML.
- The slice 0 verification is a real gate: if agent flows need the YAML format, the layout changes
  before any component is generated, which is far cheaper than discovering it in slice 5.
- Connected agents, skills and knowledge files all appear under `botcomponents/`, so adding one is
  reviewable as a file addition in a pull request.
- Because this layout does not support canvas apps, any future need for a canvas app forces the
  YAML format. Noted, not solved.
