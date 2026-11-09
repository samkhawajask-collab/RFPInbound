# RFP Response Factory: setup kit

This repository is the companion to the **RFP Response Factory** entry in the
[Copilot Studio Gallery](https://github.com/microsoft/copilot-studio-gallery). The gallery entry holds the Power Platform solution
(`RFPResponseFactory`): the RFP Orchestrator agent, 11 flows, 12 AI Builder prompts and the custom connector. This repository holds
what it needs around it:

| Folder | What it is |
|---|---|
| `release/RFPFactoryData.zip` | Unmanaged solution with the 22 Dataverse tables and 22 choices. **Import it before the gallery solution.** |
| `release/RFPResponseFactory.zip` | The gallery solution itself, byte for byte what the gallery publishes, for anyone who wants it from here |
| `gallery-submission/rfp-response-factory/` | The gallery entry as submitted: `metadata.json`, the gallery README and the exploded solution. [SUBMITTING.md](SUBMITTING.md) describes the pull request |
| `tools/` | `rfp-tools`, the Python Azure Function behind the custom connector. It does the deterministic work: document normalization, requirement IDs, rule matching, thresholds, certifications, word and page counts, compliance values, template filling |
| `scripts/` | PowerShell setup scripts (Azure, SharePoint, reference data, environment variables, flow activation) and Playwright helpers for portal steps |
| `solution/spec/` | The specs the solution was generated from: tables, flows, prompts and the connector |
| `data/RFP_Synthetic_Dataset_Pilot/` | A synthetic data set: one fictional vendor, an answer library, policy rules, SMEs, templates and four sample RFPs |
| `docs/` | Architecture, runbook and the decision records (ADRs) |

> **Sample, not production.** The output is a draft for qualified people to review. It isn't legal, contractual or pricing advice.
> Everything in `data/` is fictitious.

## What you need

- A Power Platform environment with Dataverse, and Copilot Studio capacity (Copilot Credits). Power Automate licensing for the three
  Teams card flows. The Workflows app allowed in the Teams admin center.
- An Azure subscription, for a Flex Consumption Function App (Python 3.12) and Azure AI Document Intelligence (S0).
- Microsoft 365: SharePoint and Teams.
- PowerShell 7, the Azure CLI, the Power Platform CLI (`pac`), Python 3.12 and Git. Node.js is only for the optional portal helpers.

## Set up

The scripts call your environment `dev`. They only touch the environment and the Azure names in `scripts/env.config`.

1. **Configure.** Copy `scripts/env.config.example` to `scripts/env.config`. Fill in `DEV_URL` (your environment URL), the Azure names
   (`AZURE_LOCATION`, `AZURE_RESOURCE_GROUP`, `AZURE_STORAGE_ACCOUNT`, `AZURE_FUNCTION_APP`, `AZURE_DOCINTEL`) and the SharePoint site
   (`SHAREPOINT_SITE_ALIAS`, `SHAREPOINT_SITE_NAME`). Sign in with `az login` and `pac auth create --environment <DEV_URL>`.
2. **Azure.** Create the Python environment for the tools: `python -m venv tools/.venv`, then
   `tools/.venv/Scripts/pip install -r tools/requirements.txt pytest`. Then run `./scripts/provision-azure.ps1` and
   `./scripts/deploy-tools.ps1`. The deploy runs the tests first and refuses to deploy if any fail.
3. **SharePoint.** `./scripts/provision-sharepoint.ps1` creates the site and its folders, and uploads the company content and the
   templates.
4. **Tables.** `pac solution import --path release/RFPFactoryData.zip`.
5. **The gallery solution.** Download it from the gallery entry (or use `release/RFPResponseFactory.zip`) and import it. Bind the five connection references. Set
   `hrs_RfpToolsBaseUrl` to your Function App's host **during** the import, because the connector needs it. The connection for the
   custom connector asks for the Function App's key.
6. **Settings.** Copy `scripts/settings/deployment-settings.template.json` to `scripts/settings/dev.local.json`, fill in the values,
   and run `./scripts/set-env-values.ps1 -Target dev`. The gallery README lists each environment variable.
7. **Reference data.** `./scripts/seed-data.ps1 -Target dev` loads the synthetic answer library, SMEs, policy rules, approvers,
   certifications and opportunities.
8. **Turn it on.** `./scripts/activate-flows.ps1 -Target dev`. Then publish the RFP Orchestrator in Copilot Studio and add the Teams
   channel.
9. **Try it.** Put `data/RFP_Synthetic_Dataset_Pilot/01_Inbound_RFPs/RFP-A_Cedar_Valley_Water_Authority_RFP-2026-117.docx` in the
   site's `Inbound` folder. Then ask the Orchestrator "What is the status of RFP A?". `docs/RUNBOOK.md` describes the run, the cards
   and reruns.

Some steps in the docs mention a second environment (`test`), an evaluation harness or gold data. Those are part of the original
project and aren't in this repository; skip them.

## Use your own content

Replace the synthetic data with your own answer library, policy rules, SMEs and templates, in the same table shapes
(`data/RFP_Synthetic_Dataset_Pilot/09_Dataverse_Import/Dataverse_Schema.md`). Company facts live in data, not in prompts or agent
instructions. Expect to tune the prompts (`solution/spec/prompts/`) and the policy rules for your documents.
