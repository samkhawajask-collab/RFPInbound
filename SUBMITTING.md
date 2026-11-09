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

## Fresh-environment test (2026-11-09)

The pull-request template requires this attestation. The test followed [README.md](README.md) from a fresh clone of this
repository, in a new Trial environment ("RFP Gallery Fresh"). It used new Azure resources and a new SharePoint site, and
nothing from the development environments.

| Step | Result |
|---|---|
| `RFPFactoryData.zip` import | Succeeded (6.5 min) |
| `RFPResponseFactory.zip` import (byte-identical to the gallery bundle), dependency checks on, env vars at import | Succeeded (2 min). 11 flows, 1 agent, 6 tools and hooks, 12 prompts. Every flow kept its generated `associatedData.graph` |
| rfp-tools tests and deploy | 226 passed; healthy with 56 operations |
| Connections, settings, seed data (318 rows), flow activation | All 11 flows on |
| RFP A, uploaded to `Inbound` | Stages 1-7 ran: 60 requirements, 49 responses, 37 flags, 7 gaps routed as Teams cards. After the gaps were closed with `complete-gaps.ps1`, stages 8-9 wrote the proposal package to `Output/Draft` and posted the approval card |

The test also found gaps in the setup scripts and docs, which are fixed in this repository:

- `provision-azure.ps1` failed its first run on a role-assignment lookup.
- `apply-connector.ps1` needed `-ConnectionOnly` for an environment without the RFPFactory solution.
- `activate-flows.ps1` had a strict-mode bug.
- The settings template was missing six environment variables.
- The README's step order was wrong.

## Steps to open the pull request

1. **Fork and branch.** Fork the gallery, then clone your fork and create a branch:
   ```bash
   gh repo fork microsoft/copilot-studio-gallery --clone
   cd copilot-studio-gallery
   git config core.autocrlf false   # on Windows: keeps the demo fixtures' hashes valid for check:submissions
   git checkout -b add-rfp-response-factory
   ```
2. **Add the entry.** Copy `gallery-submission/rfp-response-factory/` from this repository to `submissions/rfp-response-factory/`.
   Then run `npm ci` and `npm run check:submissions`. Don't commit the generated files under `public/bundles/` or
   `src/content/`.
3. **Commit, push and open the PR** against `main`, with the description below.

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

The attestation is backed by the fresh-environment test above. The pull request is [#114](https://github.com/microsoft/copilot-studio-gallery/pull/114).
