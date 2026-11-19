# Runbook — Proposal & RFP Response Factory

How to operate the pipeline in dev and test. Architecture lives in [ARCHITECTURE.md](ARCHITECTURE.md); decisions in
[adr/](adr/). Every command runs from the repository root in PowerShell 7, signed in with `az login` and `pac auth`
as the admin account. Imports go only to the environments in `scripts/env.config` (hard rule 3).

## 1. Start a run

1. Put the RFP's file in the SharePoint site's `Inbound` folder (Word or PDF). The receive flow (`RFP - Receive
   Inbound Document`) polls every minute, matches the file to an `hrs_opportunity` by RFP number (or by buyer name for
   an addendum), creates the RFP, revision 0 and document rows, and sets the RFP to Running.
2. `RFP - Run Pipeline` runs every stage after `hrs_stage`. Only one pipeline run happens at a time per environment;
   other RFPs queue (ADR-0018, ADR-0029).
3. An addendum is a second file for the same RFP. It becomes revision N+1:
   - **RFP waiting or finished:** the run restarts from stage 3.
   - **RFP Running:** the pipeline picks it up at the start of stage 7 or the end of stage 9 (ADR-0010
     amendment 1, ADR-0026).

## 2. Watch a run

- **Ask the RFP Orchestrator**, for example "What is the status of RFP A?". It's in Copilot Studio; use the test
  chat until the Teams channel is added (§9). It reports:
  - the stage, the status and the waits (SME gaps, OCR verifications, approval)
  - the last 8 run-log rows with their credits
- **Or read `hrs_runlog`.** There is one row per stage pass: start, end, Succeeded or Failed, prompt credits,
  and the error or wait message. Rows are never deleted; reruns add rows.
- **Status values** (`hrs_rfp.hrs_status`): Running, Awaiting human (stage 2 OCR, stage 7 gaps, stage 9
  approval), Completed (approved and filed), Failed.

## 3. Human waits

Every card goes to `hrs_PilotRecipientUpn` in the pilot (Q6). The card names the routed or backup person. For each
card (ADR-0027):

1. It waits `hrs_ReminderAfter` (default `PT24H`).
2. If unanswered, a reminder message is posted and the card goes again.
3. After `hrs_EscalateAfter` (default `PT24H`), an escalation message names the backup (the category's backup SME,
   or the next approval level) and the card goes a third time.

If none is answered:

| Card | Outcome |
|---|---|
| Gap | Becomes Overdue: it stays a gap and appears on the approval card |
| OCR verification | Logs "Verification overdue". The flag stays open and the run waits; OCR text is never accepted by default |
| Approval | Logs "Approval overdue". The RFP stays Awaiting human |

If a card's row was removed by a rerun or a failed stage, an answer gets the reply "withdrawn ... nothing was
recorded". To change the timings, set the two environment variables' values (ISO 8601) and turn the three card flows
off and on: `scripts/failure-drills.ps1` shows how.

## 4. When a run fails

| Run-log message | What happened | What to do |
|---|---|---|
| "Stage N failed unexpectedly ... retrying once." | A non-gate failure. Rows the attempt created were removed; the stage reruns automatically (ADR-0026) | Nothing, unless a second row follows |
| "... it already failed once, so the run stopped." | The retry failed too. RFP Failed | Open the flow run named in the row, fix the cause, then rerun from stage N (§5) |
| A gate message (G1-G6), e.g. "governed narrative is 21 pages, limit 20" | A gate stopped the run. Gates never retry or waive | Fix the input or the content, then rerun from that stage |
| "The run failed before a stage started" | Reading the source file or company content failed | Check that the source document named on revision 0 exists in SharePoint |
| "An addendum arrived during the run; restarting from stage 3" | Not a failure | Nothing |

- **Document Intelligence throttling** is retried inside rfp-tools: 3 times with backoff, honoring
  `Retry-After`. After that, rfp-tools answers 503, and the flow's retry policy tries 3 more times.
- **Copilot Credit capacity exhausted:** agent flow runs are blocked, and the pipeline stops at the next action.
  Restore capacity in the Power Platform admin center, then rerun from the failed stage. This was not drilled; see
  §8.

## 5. Rerun from a stage

- **Ask the Orchestrator**, for example "Rerun RFP B from stage 6". It confirms the key and the stage first.
  - **Refused when:** the pipeline is running, the RFP is filed, or the stage is outside 1-9 or not reached yet. Both
    the tool and the Pre tool use hook check this.
  - **Otherwise:** it removes the rows created since that stage's latest pass and restarts the run.
- **Or run the script:** `./scripts/rewind-run.ps1 -Key B -FromStage 6`. Same rule. It also cancels the card flow
  runs of the gaps and approvals it removes.
- **Stage 3 reruns from stage 2.** Stage 2 or earlier resets `hrs_currentrevision`, so every addendum is applied
  again.
- **Stage 9 needs a saved package.** If it fails with "No saved package from stage 8", the RFP was assembled before
  ADR-0032. Rerun it from stage 8.
- **A flow can start late.** On 2026-11-01 in dev, a card flow started about 6 minutes after its row was created, and a
  rewind showed no `RFP - Run Pipeline` run for at least 4 minutes. The cause wasn't found. Wait at least 10 minutes
  before treating a run as stuck. Don't rewind again sooner: a second rewind in that window left RFP B Failed at stage 8.
- **Answering cards by script:** `scripts/ui/answer-cards-in-teams.js` answers OCR, gap and approval cards in Teams as
  the pilot recipient. Dev and test post to the same chat with the same IDs, so set `SINCE` to the run's start and check
  the target's rows afterward. Gap answers default to "ok", which is only for testing the flow.

## 6. Evaluation runs (dev)

| Command | What it does |
|---|---|
| `./eval/run.ps1 -Rfp A` | Full scored run: reset, upload, OCR and gaps closed by script (ADR-0020), score |
| `./eval/run.ps1 -Rfp D` | Base document, then Addendum No. 1 |
| `./eval/run.ps1 -Rfp A -FromStage 5 -Note "..."` | Rerun from stage 5 and score |
| `./eval/run.ps1 -Rfp A -ScoreOnly` | Score the run as it stands |
| `./eval/run-all.ps1` | The Slice 7 exit run: A, B and D in one pass |
| `./eval/scale/run.ps1 [-FromStage N]` | SCALE-500, unscored, with the card flows off during the run (ADR-0029) |

`./eval/run-with-cards.ps1 -Rfp D -Target test -AddendumDuring` does a full run with every card answered in Teams by
Playwright, so the real card flows run. Gap answers are "ok", so it tests the flows, not answer quality.
If its driver stops, add `-Resume -Since <UTC time the run started>`. Nothing is reset, and an addendum already applied
isn't uploaded again. A card posted before local midnight shows a date in Teams, and the script refuses it, so rewind
from that stage to post a new one.

`node scripts/ui/ask-orchestrator-teams.js "question"` asks the Orchestrator in Teams; it needs `PP_ENV_ID` and `BOT_ID`.
The portal's "View in Microsoft Teams" button often opens nothing. Once the agent is added, set `TEAMS_CHAT=1` to open it
from the chat list, or `TEAMS_DEEP` to the link an earlier run printed. Both agents are added now, and they have the same
name in the chat list, so use `TEAMS_DEEP` and check which environment wrote the audit row.

**A flow can come up off after an update.** On 2026-11-02 in dev, `RFP - Rerun Stage` was off after 2 updates and Explain Flag
after 1, and later Get Run Status after 1, each reported as started. Run `check-flows.ps1` a minute or so after an update, and
start any that are off. `activate-flows.ps1 -Target dev` failed on 2026-11-02 with "The property 'workflowid' cannot be found",
so start them with the flow API or the portal until that's looked at. It works with `-Target test`.

**A missing audit row means a hook failed, not that it was skipped.** Hooks fail silently, so check the run history of
`RFP - Hook Post Tool Use`. On 2026-11-02 in test, Teams conversation IDs overflowed `hrs_correlationid` (ADR-0033).

**Count `Tool run:` rows for the audit (ADR-0034).** Each tool writes one itself, with the caller, channel and conversation.
In Teams the caller is the Teams user ID (`29:...`), which differs per agent, not the Entra object ID. In the pilot that caller
is the admin account, so no mapping exists; revisit this before anyone else uses the agent. If a rerun reports that
the audit record could not be written, read the failed `RFP - Rerun Stage` flow run: nothing changed, and a retry fails the
same way until the write is fixed. `Tool call:` rows come from the Post tool use
hook, carry the Entra object ID and can be missing. On 2026-11-02 in test, two status questions in a Teams chat started no
hook run. After the next import, the hook fired in the same chat for two calls. The cause is **[UNVERIFIED]**, and the record
no longer depends on it.

**An addendum after gaps are answered (ADR-0033).** A requirement whose text is unchanged keeps its SME answer with no new
card. A changed or new one gets a card. RFP D on such a run scored flag precision 0.80 before this fix
(`eval/reports/20261102-033426-D-test.md`) and 0.923 after it (`eval/reports/20261102-151351-D-test.md`).

Reports go to `eval/reports/`. Each has a Cost and latency section:
- prompt credits and machine minutes per stage, against ARCHITECTURE.md §14.2
- the agent-flow action estimate and the credit budget (ADR-0028)

## 7. Reconcile credits

The report's figures come from AI Builder's `costAsCopilotCredits` per prompt call and an estimate of agent-flow
actions from the run history. Once a week, and after a slice's exit run:

1. Power Platform admin center → Licensing → Copilot Studio → Download report. Choose Copilot Credits, a look-back window and
   Agent-Level Credit Consumption. Select Start, then Download once the button changes.
2. Note the "Last refreshed" date on the Prepaid capacity card. Flow-action figures run only to the end of that day (UTC), but prompt
   figures can run later.
3. From `eval/`, with the tools venv:

   ```bash
   ../tools/.venv/Scripts/python.exe -m rfp_eval.cli reconcile --url <environment url> --target test \
     --environment "<environment name as the file shows it>" --report <downloaded csv> --actions-until <the next day>T00:00:00Z
   ```

   It sums Billed plus Non-billed credit, because pipeline credits appear as non-billed. It reads every Run Pipeline run in the flow
   history (about 28 days), stage reruns included, and writes `eval/reports/<timestamp>-<target>-reconcile.md`. It takes about 20
   seconds per run: about 10 minutes for test and an hour for dev. Run it in the foreground. Exit code 1 means a gap over 10%.
4. Record any gap of more than 10% in ADR-0028.

The file has no dates, so it can't give one day's figures, and the window must match the run history. Microsoft Learn says this data is
aggregated daily and can be delayed, and gives no refresh schedule. The last reconciliation is in ADR-0028: test matched within 1%, dev's
actions within 1%, and dev's prompts 7.9% short, from a source outside the pipeline that isn't known.

## 8. Failure drills

Run in dev only. Results as of Slice 7:

| Drill | How | Result |
|---|---|---|
| a. Transient rfp-tools errors are retried | App setting `RFPTOOLS_FAULTS=stage-relation-updates:4` on the Function App, rerun RFP A from stage 4, then remove the setting | The 503s were retried 3 times, then the stage failed (with 4 faults) |
| b. A failed stage undoes its rows and retries once | Same run | Stage 4 row Failed "retrying once". The retry passed; the run scored PASS (`eval/reports/20261031-230025-A-dev.md`) |
| c. A gate stops without a retry | `./scripts/failure-drills.ps1 -Drill c` | PASS: G6 "21 pages, limit 1". RFP Failed, one stage-8 row, no retry |
| d. Rerun from Failed | `./scripts/rewind-run.ps1` or the Orchestrator's Rerun Stage | Used after drills b and c, and for SCALE-500 |
| e. Reminder, escalation, overdue | `./scripts/failure-drills.ps1 -Drill ef` | PASS for the approval, OCR and gap cards (2-minute timings): reminder, escalation, then the overdue outcome |
| f. Superseded card | Same drill | PASS: approval row deleted while its card waited; the flow posted the withdrawn notice |
| g. Addendum during a run | `./scripts/failure-drills.ps1 -Drill g` (a scored RFP D run) | PASS: Addendum No. 1 uploaded at stage 3, restart logged, revision 1 applied, run scored PASS (`eval/reports/20261101-031647-D-dev.md`). Two earlier reports carry this drill's note but aren't drills: see below |
| h. Hooks | Ask the Orchestrator to rerun RFP A from stage 12; ask for a status | PASS in dev and test (2026-11-01): Pre tool use denied ("Stage must be from 1 to 9"), Rerun Stage never ran. Post tool use wrote the audit row |
| Capacity exhausted | Not drilled: it can't be simulated safely in a shared tenant | Behavior per Learn: agent flows are blocked, the agent still answers |

**Mislabeled drill g reports.** The drill script crashed on two attempts while polling, because the harness had just
reset RFP D and there was no row yet. No addendum was uploaded mid-run, so the harness ran its normal addendum phase
under the drill's note. The script is fixed. Read these two reports as ordinary RFP D runs:
- `eval/reports/20261101-020152-D-dev.md`: PASS.
- `eval/reports/20261101-022915-D-dev.md`: FAIL, compliance accuracy 0.917 against 0.92 (THPA-028, -035, -039 and -043
  were Clarify, not Full). It overlapped the exit run's RFP A. The cause wasn't investigated: the two later RFP D
  runs on the same code scored 1.000 and 0.979, so it may be run-to-run variation.

**Hook flows found off (2026-11-01).** The two hook flows were off in the export taken after the exit run, so they
may have been off during it. Hooks fail open and the pipeline doesn't use them, so scores aren't affected, but while
they're off Rerun Stage isn't validated by the hook and tool calls aren't audited. They were turned on with
`apply-flows.ps1 -Only`, and drill h was repeated: PASS.

The cause wasn't found. They were on at 01:07 UTC and off by the 03:17 UTC export. No project script changes flow state
in that window, and the audit log has no rows for them. Repeating the one related step, adding the agent to the
solution with required components, didn't turn them off, though the original was a first-time add.

So `./scripts/check-flows.ps1 -Target dev|test` now fails if any flow is off or missing. `eval/run.ps1` runs it before
a full run. Run it after any deploy or import too.

**Gallery review fixes (PR 114, 2026-11-15).** Drills for the nine review findings, run in dev on 2026-11-15 after
`apply-flows.ps1`. Cards were answered as the pilot recipient with `scripts/ui/drill-card.js` and
`answer-cards-in-teams.js`; reruns went through the Orchestrator (`ask-orchestrator.js`), because an agent flow's Request
trigger can't be called directly. RFP A and B are filed, so they can't be rerun; S (waiting at stage 9) and D were used.
D and S were restored afterwards: the drill revisions, files and decoys were removed, D's approval was approved (Completed,
revision 1), and S is Awaiting approval at stage 9 again.

| Drill | How | Result |
|---|---|---|
| i. Overlapping keys | Draft decoys `PROP-S~hX-001` and `PROP-A~hX-001` (codes RFPs `S-X` and `A-X` would use). Orchestrator reran S from stage 9. D's six gap cards were answered | PASS: both decoys survived `Rerun_Answers`; D's answers created `PROP-D-THPA-<reqId>` and linked them to the gaps |
| j. Stale approval after an addendum | D at stage 9 with a Pending approval; an addendum copy uploaded to `Inbound`, then Approve pressed on the old card | PASS: revision 4 created, the Pending approval deleted, D restarted at stage 3. The card flow ran `Tell_Withdrawn`; `Record_Decision`, `File_Package` and `Complete_RFP` were skipped; `Output/Filed` unchanged (3 D files); D not Completed |
| k. Blank or unchanged OCR correction | Test OCR flag on D THPA-005. Cleared text + Save my correction; then the unchanged text + Save; then Text is correct | PASS: Teams refused the blank submit ("Enter the requirement text, or choose Text is correct."). The unchanged text was not saved: the flag stayed open, "not saved" was posted, the card came back. Text is correct resolved it ("Verified: confirm"). D, waiting at stage 7, was not resumed by it |
| l. Rerun after an overdue card | S had "Approval overdue" (11-05) after its "Stage 9 Approval" pass (11-02) and a Pending approval from that pass. Orchestrator reran S from stage 9 | PASS: the 11-02 approval was deleted (the old cutoff, the overdue row, would have kept it) |
| m. Two addenda together | Two copies of D's addendum uploaded in the same second | PASS: revisions 2 and 3. The second run's first action started at 23:27:30.55, after the first run ended (23:27:30) |
| n. Quick rejection | Reject pressed on S's new approval card as soon as it was posted | First run: the rejection held (stage 7 / Running; after creating the approval row the run wrote only its run log), but the rebuilt stage 9 failed twice with DuplicateRecordEntityKey: hrs_approval allows one row per RFP and the Rejected row was still there, so S ended Failed. Fixed: the reject branch logs "Approval rejected" with the comments and deletes the row. Repeated after the fix: PASS, S rebuilt stages 8-9 and posted a new card |
| o. Rerun while a cleanup holds the lock | A fresh "Rerun cleanup in progress" row on S, then a rerun asked for | PASS: "Not rerun: another rerun of this RFP is still removing rows"; S unchanged. On the real rerun the lock was written and released (Succeeded) 4 seconds later |
| p. Early answers | D's stage 7 rerun | PASS for the order: all six gaps created 23:32:44, Awaiting human written 23:32:46; the last gap answer resumed D (Awaiting human at stage 7). An answer arriving while gaps are still being written wasn't forced |

Second Copilot review (2026-11-16), checked in dev the same way: two Orchestrator sessions confirming a rerun of S one second apart both wrote claims; the earlier one ran, the later released its claim (Skipped) and was refused. Trigger concurrency on Rerun Stage was rejected at activation (InvalidConcurrencyConfiguration: not supported with a synchronous Response). An addendum for a Completed D went through RFP_After_Revision and restarted D at stage 3. Approving D's next card went through RFP_Before_Complete and If_still_current to Complete_RFP. The superseded-during-filing branch wasn't forced. D was restored afterwards (revision 1, Completed); S is Awaiting approval at stage 9.

Third and fourth Copilot reviews (2026-11-17), checked in dev: an evaluation of the RFP Orchestrator in Copilot Studio (8 cases, General quality and Compare meaning, average 88%) was exported to gallery-submission/.../evaluations/; its case "x' or hrs_rfpkey ne 'x" got "No RFP has that key", which checks the apostrophe escaping through the live agent. For the stage 9 recheck, S's page limit was set to 1 and its narrative edited while the card was open: the stage 9 rerun re-counted the current file and failed G6 ("governed narrative is 15 pages, limit 1"). With the limit restored (30), a rerun from stage 9 passed the recheck and posted a card with the re-measured 15 pages.

Fifth Copilot review (2026-11-19): filters built from stored strings are escaped like agent input (odata_literal): Existing_RFP (Receive), the PROP-<key>- prefix (Rerun_Answers, the stage cleanup, OWN_DRAFTS), Existing_Proposal (Route Gap, including the buyer's requirement ID), Signatory and Approver, and the key in reset-run.ps1 and rewind-run.ps1. Checked against dev Dataverse: each filter with O''Brien or R''7 returns 200; the unescaped form returns 400.

**Reading a failed RFP D run.** Compliance for D can vary between runs: see ADR-0021. Check the misses against
spurious AMBIGUOUS and CONTRADICTION flags before suspecting a regression.

**Slice 7 exit run** (`./eval/run-all.ps1`, 2026-11-01, no reruns): A, B and D all PASS, with zero must-not-use
violations. Scores and credits are in ADR-0028.

## 9. Orchestrator agent

- **What it is:** `hrs_RFPOrchestrator`, on the GitHub Copilot harness. It has four tools (agent flows `RFP - Get
  Run Status`, `Explain Flag`, `Explain Answer`, `Rerun Stage`) and two hooks: Pre tool use on Rerun Stage, and Post
  tool use on all tools (ADR-0030).
- **Changing tools, instructions or hooks:** use the portal (Build tab, command bar … → Hooks), or
  `node scripts/ui/setup-orchestrator.js`. Then save and publish. Instructions are only kept after Ctrl+S.
- **Testing:** `node scripts/ui/ask-orchestrator.js "question"` uses the test chat.
- **Audit rows:** `hrs_runlog` rows named "Tool call: <tool>". They hold the parameters, the user's Entra object
  ID, the channel and the conversation ID.
- **Teams channel:** published in dev and test, available in Microsoft Teams only. Availability can't be changed after
  publishing, and the portal preselects Copilot and Teams. Add it with `node scripts/ui/add-teams-channel.js`; test gets
  it from the managed package. Nobody has opened the agent in Teams yet.
- **Rerun confirmation:** the agent asks first and uses Rerun Stage only in a later turn, after a yes. Tested in dev and
  test on 2026-11-01: a no changes nothing; a yes runs the hooks and the rerun; a yes for stage 12 is denied by the hook.
  This is an instruction, so it depends on the model. The hook is what enforces valid stages.
- **Audit gap:** a call the Pre tool use hook denies writes no `hrs_runlog` row, because Post tool use doesn't run.
  Denied attempts appear only in the agent's conversation history.

## 10. Portal sign-in for scripts

`scripts/ui/*.js` use a persistent Chromium profile in `scripts/ui/profile` (git-ignored).
1. When a script stops at the sign-in page, sign in once in its window as the admin account and choose "Stay signed
   in".
2. Passwords are never stored in files.
3. The same profile serves Teams (`scripts/ui/teams-cards.js`), which is still needed to check how a card's inline
   image renders (ADR-0025, open).

## 11. Deploy order

Run these between pipeline runs, never while one is in flight: a redeployed trigger loses the run's handoff.

1. `./scripts/deploy-tools.ps1` (rfp-tools; restarts the Function)
2. `./scripts/apply-connector.ps1` (new operations)
3. `./scripts/apply-prompts.ps1`
4. `./scripts/apply-flows.ps1` (`-Only <names>` for a subset)
5. `./scripts/sync-solution.ps1`, then pack, `pac solution check`, import (dev or test only)

**Test environment.** It takes a managed package exported from dev (`pac solution export --managed true`, saved as
`solution/build/RFPFactory_managed.zip`). The connector can't import until `hrs_RfpToolsBaseUrl` has a value, so
`scripts/settings/test.local.json` (git-ignored) must set one. Test is shared with Project Operations, whose upgrades
block imports while they run.

Test has its own rfp-tools and SharePoint site (ADR-0031). To set it up from nothing:

1. `./scripts/provision-azure.ps1 -Target test`, `./scripts/provision-sharepoint.ps1 -Target test`, `./scripts/deploy-tools.ps1 -Target test`
2. Fill `scripts/settings/test.local.json` from test's own site and Function App, with `hrs_RfpToolsBaseUrl` set to test's host.
3. Import the managed package, then create the connections: `scripts/ui/create-connections.js` with `PP_ENV_ID` and `PP_CONNECTORS`, and `./scripts/apply-connector.ps1 -Target test -CreateConnection`.
4. Add the connection IDs to the settings file, then run `./scripts/set-env-values.ps1 -Target test` and `./scripts/seed-data.ps1 -Target test`.
5. `./scripts/activate-flows.ps1 -Target test`. Importing with bound references didn't turn the flows on.
6. `./eval/run-all.ps1 -Target test -Note "..."`

Set the real `hrs_RfpToolsBaseUrl` before the first import if you can. On 2026-11-01 the first import used a placeholder
and the connector still reached test's Function App after the value changed and the solution was re-imported. Learn says
a connector reads the value when saved, so which of those steps refreshed it isn't established.

Slice 7 acceptance in test, 2026-11-01: A, B and D all PASS with zero must-not-use violations
(`eval/reports/20261101-130054-A-test.md`, `20261101-131119-B-test.md`, `20261101-133230-D-test.md`). Dev's run rows and
Inbound and Output/Draft files were unchanged by the run.
