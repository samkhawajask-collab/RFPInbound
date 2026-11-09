# ADR-0015 — Leaf steps are prompts called by agent flows

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Solution architecture (direction set by the project owner: "spike first, prefer prompts")
- **Supersedes:** [ADR-0012](0012-harness-selection-github-copilot-harness.md) for the five pipeline steps
- **Amends:** [ADR-0001](0001-deterministic-workflow-spine-agents-at-the-leaves.md): the spine stays; the leaves become prompts
- **Related:** [ADR-0011](0011-solution-source-layout-and-regeneration.md), [ADR-0014](0014-dataverse-schema-from-spec.md)

## Context

ADR-0001 put a deterministic workflow spine in charge and called **agents** at the leaves through the
agent node. ADR-0012 put those agents on the GitHub Copilot harness. Research for Slice 1 (Microsoft
Learn, 2026-10-28) found three problems with that combination:

- Agent flows and the agent node are documented for the **standard harness**. Whether the agent node
  can call a GitHub Copilot-harness agent isn't documented.
- The agent node returns **free text** ("agent response") only. Every leaf step in this pipeline is
  one input → one structured output (a category code, a verdict, a fired/not-fired decision with a
  reason, a response with a code), so free text would have to be parsed and validated in the flow.
- `Run a prompt` returns **JSON**, which matches that shape.

The project owner chose: spike first, prefer prompts.

## What the spike established (dev, 2026-10-28)

Every component was created from source with the Web API. There was no portal step.

1. **Prompts can be generated from a spec.** A prompt is an `msdyn_aimodel` on the `GptPowerPrompt`
   template plus `msdyn_aiconfiguration` rows. The prompt itself (literal text, named input
   variables, a **strict JSON Schema** for the output, examples and model parameters) is the
   `msdyn_customconfiguration` JSON, `version: "GptDynamicPrompt-2"`. The working lifecycle:
   1. POST the model with `msdyn_TemplateId@odata.bind` to the `GptPowerPrompt` template.
   2. POST a training configuration (`msdyn_type = 190690000`) carrying the custom configuration.
   3. Call the bound action `Train` with `{ "version": "1.0" }`. The status goes to 6 (Trained).
   4. POST a run configuration (`msdyn_type = 190690001`) with the same custom configuration, bound
      to the model and to the trained configuration (`msdyn_TrainedModelAIConfigurationPareId`).
      **Don't** set the iteration-number columns; the API rejects them.
   5. Call the bound action `PublishAIConfiguration` with `{ "version": "1.0" }`. The status goes to
      7 (Published), and the model's active run configuration is set.
   Unpublishing is `UnpublishAIConfiguration`, which also takes `version`.
2. **An agent flow can be generated from a spec.** It's a `workflow` row with `category = 5`,
   `modernflowtype = 1` (CopilotStudioFlow), `type = 1` and JSON `clientdata`. Activate it with
   `statecode = 1, statuscode = 2`, then start it in Power Automate with `/start`.
3. **A flow calls a prompt like this:** an `OpenApiConnection` action, `operationId:
   aibuilderpredict_customprompt`, `apiId: providers/Microsoft.ProcessSimple/operationGroups/aibuilder`,
   over the **Dataverse** connection. `recordId` is the model ID, and the inputs are
   `item/requestv2/<InputId>`. The output is at
   `body/responsev2/predictionOutput/structuredOutput`: already typed, so no ParseJson is needed.
   The raw text is also available at `.../text`.
4. **Measured result:** a requirement was classified as `{"categoryCode":"SEC","confidence":0.98}` on
   `gpt-5-chat-2025-07-14` with temperature 0. It used 1,586 tokens and **3.0 Copilot credits** per call.
5. **Source round-trip in the classic XML layout works.** The flow unpacks to
   `Workflows/<Name>-<GUID>.json` plus a `.data.xml` (which records `<ModernFlowType>1`). The prompt
   unpacks into `Other/Customizations.xml` under `<AIModels>`, with both configurations and the full
   custom configuration, including the JSON Schema. Repack → import into dev succeeded.
6. **One limitation:** the Power Automate `triggers/manual/run` endpoint doesn't pass a body to a
   `Request`/`Button` trigger (`TriggerInputSchemaMismatch` when inputs are required). Pipeline flows
   start from SharePoint triggers and child-flow calls, so this only affects ad-hoc testing.

## Options considered

### Option A — GitHub Copilot-harness agents through the agent node (ADR-0012)
- **Against:** undocumented for agent flows; free-text output; 5 credits per agent action on top of
  model use; agents are the hardest component type to generate from source.

### Option B — Standard-harness agents through the agent node
- **For:** a proven `pac copilot create` / `gpt.default` route on this tenant.
- **Against:** still free-text output that has to be parsed; agent-action billing; and an agent adds
  orchestration the step doesn't need, because the flow already decides *when*.

### Option C — Prompts called by agent flows *(chosen)*
- **For:** typed output enforced by a strict JSON Schema; temperature 0; one call per step; generated
  from a spec and round-tripping in the existing source layout; no agent orchestration to go wrong.
- **Against:** prompts have no tools and only Dataverse as knowledge, so company content reaches
  Drafting as **input text**. That's fine at pilot scale, where the content documents are small.
  Prompt versions are immutable once published, so a change means a new training and run
  configuration (handled by the apply script).

## Decision

**Option C.** The pipeline's judgment steps are AI Builder prompts with JSON output, called from
agent flows:

| Step | Prompt (as built, `solution/spec/prompts/`) |
|---|---|
| Stage 1: RFP facts | `RFP Extract RFP Facts` |
| Stage 2: classify | `RFP Classify Requirements` |
| Stage 4: relations | `RFP Judge Relations` (the whole set in one call) |
| Stage 5: pick answer | `RFP Select Answers` (sees **approved** candidates only) |
| Stage 5: draft | `RFP Draft Responses` |
| Stage 6: adjudicate | `RFP Adjudicate Rules` |
| Stage 7: clarify | `RFP Draft Clarifications` |
| Stage 8: narrative | `RFP Draft Section` (ADR-0016) |

Prompts work on **batches**: one call covers many requirements, which keeps flow actions and credits
down. Each output item echoes its `reqId`, and the flow checks every input ID came back exactly once.
`Predict` (bound to `msdyn_aimodel`) needs `source` =
`{"consumptionSource":"PowerAutomate","partnerSource":"AIBuilder","consumptionSourceVersion":"1"}`
when called directly; `apply-prompts.ps1 -Test` uses it to smoke-test every prompt.

- Prompts are defined in `solution/spec/prompts/*.json`, applied to dev by `scripts/apply-prompts.ps1`
  following the lifecycle above, and reconciled by `scripts/sync-solution.ps1` (the ADR-0014 loop).
- Instructions are short and testable. Company facts arrive only as inputs, never in the instruction
  text (CLAUDE.md writing rules).
- **`RFP Orchestrator`**, the one conversational agent, is deferred to Slice 7. Its harness (standard
  vs. GitHub Copilot) is decided then, and the agent-node test belongs to that slice.
- Hooks (§10.2) attach to agents, so with no agents in the pipeline they don't apply in Slice 1. Their
  audit duty moves into the stage flows, which write `hrs_runlog`. §10.1's closed controls never
  depended on hooks, so nothing weakens.

## Consequences

- **Cost:** about 3 credits per prompt call for a short input, rising with input size. The §14.3
  estimate is re-based on measured per-call costs once RFP A runs end to end.
- **ADR-0012 is superseded for the pipeline.** It stays the reference for the Orchestrator decision
  in Slice 7.
- **ADR-0011 stands:** agent flows and prompts both round-trip in the classic XML layout (A2 retired
  for these component types).
- Bad output is a schema violation the platform rejects, not prose the flow has to interpret. The
  flow still validates values against the reference data, for example that a category code exists.
