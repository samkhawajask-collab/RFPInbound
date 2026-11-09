# Submitting to the Copilot Studio Gallery

How the entry in `gallery-submission/rfp-response-factory/` gets into
[microsoft/copilot-studio-gallery](https://github.com/microsoft/copilot-studio-gallery), following its
[CONTRIBUTING.md](https://github.com/microsoft/copilot-studio-gallery/blob/main/CONTRIBUTING.md).

## What's already been checked

These ran on 2026-11-09 against gallery commit `8ce50e4`:

| Check | Result |
|---|---|
| `npm run check:submissions` | Pass (37 submissions) |
| `npm run import:submissions` | Pass. It parsed 1 agent, 11 workflows and 2 hooks, with `RFP - Run Pipeline` as the hero workflow |
| `npm run check`, `npm run build` | Pass |
| `npm test` | 6 tests fail on Windows, and they fail the same way without this entry: Windows refuses to create symlinks without Developer Mode (EPERM). CI runs on Linux |
| `pac solution check` on the gallery's generated ZIP | 0 critical, 0 high. The medium findings are 66 missing-table dependencies (expected: the tables are in `RFPFactoryData`) and 3 connector warnings |

## Still to do before opening the pull request

1. **Fresh-environment import (required by the PR template).** Use a new environment with only the documented
   prerequisites (see [README.md](README.md)). Import `release/RFPFactoryData.zip`, then `release/RFPResponseFactory.zip`,
   without skipping dependency checks. Set `hrs_RfpToolsBaseUrl` during the import. Then run RFP A end to end to confirm
   the flows work with the generated preview graphs in their trigger metadata. That last point isn't verified yet.
2. **Fork and branch.** Fork the gallery, then clone your fork and create a branch:
   ```bash
   gh repo fork microsoft/copilot-studio-gallery --clone
   cd copilot-studio-gallery
   git config core.autocrlf false   # on Windows: keeps the demo fixtures' hashes valid for check:submissions
   git checkout -b add-rfp-response-factory
   ```
3. **Add the entry.** Copy `gallery-submission/rfp-response-factory/` from this repository to `submissions/rfp-response-factory/`.
   Then run `npm ci` and `npm run check:submissions`. Don't commit the generated files under `public/bundles/` or
   `src/content/`.
4. **Commit, push and open the PR** against `main`, with the description below.

## Pull request description

> Adds **RFP Response Factory**: the RFP Orchestrator agent (4 workflow tools, 2 hooks), 11 flows and 12 AI Builder prompts that
> turn an inbound RFP into a requirements matrix, a grounded draft, a compliance matrix, SME gap routing and one approval.
>
> **Prerequisites (in the README):** the `RFPFactoryData` tables solution, imported first; the `rfp-tools` Azure Function; Document
> Intelligence; a SharePoint site. All are in https://github.com/samkhawajask-collab/RFPInbound. The tables ship separately because
> they exceed the 512 KB `customizations.xml` limit.
>
> **Note:** the workflow canvases are generated from each flow's definition, not saved by the designer. The README says so.
>
> ## Solution submission
>
> - [x] I successfully imported the submitted solution into a fresh environment containing only its documented prerequisites,
>   without skipping dependency checks.
>
> ## Managed app submission
>
> N/A

Tick the attestation only after step 1 succeeds.
