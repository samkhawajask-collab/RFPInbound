# ADR-0027 — Human waits get a reminder and an escalation; superseded cards are answered with a notice

- **Status:** Accepted
- **Date:** 2026-10-31
- **Deciders:** Solution architecture, with the user's approval in the Slice 7 plan
- **Related:** [ADR-0018](0018-one-pipeline-flow-chained-through-dataverse.md), [ADR-0020](0020-scoring-runs-complete-human-waits-by-script.md), [ADR-0025](0025-ocr-confidence-and-verification-queue.md), [ADR-0026](0026-failure-handling-and-stage-reruns.md), ARCHITECTURE.md §9, §14.2 and §14.4, open question Q9

## Context

ARCHITECTURE.md §14.4 promised a reminder at 24 hours and an escalation to the backup person at 48 hours
for gap and approval cards. Through Slice 5:

- **Gap card:** waited 2 days, then marked the gap Overdue.
- **OCR verification and approval cards:** had no timeout handler, so an unanswered card left the run waiting
  forever with nothing in the run log.
- **The directory's backup SME:** `hrs_category.hrs_backupsmeid` was never used.

Q9 asked how a card made obsolete by a rerun should be withdrawn. In Slice 1, an answer to such a card went to
a cancelled flow run and was lost without notice.

**Platform facts (checked 2026-10-31):**
- The Teams connector's *Post adaptive card and wait for a response* returns no message ID.
- *Update an adaptive card in a chat or channel* needs a message ID from *Post card in a chat or channel*.
- That non-waiting post *"cannot be combined with trigger When someone responds to an adaptive card"*.
- The trigger *"works only in a default environment"*, and dev is not the default environment.

Sources: [Teams connector](https://learn.microsoft.com/en-us/connectors/teams/).

## Options considered

**Reminders.**
- (a) A scheduled sweeper flow that finds overdue rows.
- (b) **The card flow's own wait times out and re-posts.**

With (b), the state stays in one flow run per card, as today, and no polling is needed. **(b) is chosen.**

**Superseded cards.**
- (a) Post with the non-waiting action, keep the message ID, and update the card to "Withdrawn". This needs the
  response trigger, which doesn't work outside the default environment and can't be combined with that action.
  Ruled out by the platform.
- (b) **The card flow checks that its row still exists once an answer arrives, and if not, tells the responder
  the card was withdrawn.** **(b) is chosen**, plus the existing cancellation of card runs by
  `rewind-run.ps1`.

## Decision

**One wait shape for the gap, OCR verification and approval cards** (`wait_with_escalation` in
`tools/flowgen/build.py`):

1. Post the card and wait up to `hrs_ReminderAfter`.
2. No answer: post a reminder message and the card again, and wait up to `hrs_EscalateAfter`.
3. Still no answer: post an escalation message naming the backup, and the card a third time, and wait up to
   `hrs_EscalateAfter`.
4. Whichever card is answered first wins.

Both durations are new environment variables (ISO 8601, default `PT24H`), so a drill can set minutes. Every
message and card still goes to `hrs_PilotRecipientUpn` in the pilot (Q6); the routed and backup people are
named in the text.

**Who is named as the backup.**
- **Gap:** the category's backup SME (`hrs_backupsmeid`).
- **Approval:** the next approval level up (the approver table has no backup column), or "this is the top
  level".
- **OCR:** no person is named. The message says the run stays paused.

**When no card is answered at all.**
- **Gap:** status Overdue. The requirement stays a Gap and appears on the approval card (§14.4).
- **OCR verification:** a run-log row "Verification overdue". The flag stays open and the run waits. OCR text is
  never accepted by default (§6.3).
- **Approval:** a run-log row "Approval overdue". The RFP stays Awaiting human.

**Superseded cards.** After an answer, each card flow re-reads its row (gap, flag or approval). If the row is
gone, it posts "The card for requirement X was withdrawn because the run was reprocessed; nothing was recorded"
and stops. A row is gone when ADR-0026's undo rule (failure handler or `rewind-run.ps1`) or stage 3's addendum
reprocess removed it. `rewind-run.ps1` also cancels the card runs it orphans, so those cards stop accepting
answers.

## Consequences

- No card can leave a run waiting silently. Every outcome (answered, overdue, withdrawn) is recorded or
  announced.
- Up to three cards appear in Teams per wait. The first two stop accepting answers once their wait times out
  ([UNVERIFIED]: how Teams presents a timed-out card; checked in the drill).
- §14.2's 3-business-day SME bound is approximated with calendar durations: 24 h + 24 h + 24 h by default.
  Business-day arithmetic is deferred.
- **Verified 2026-10-31:** the wait action's `limit.timeout` takes the environment-variable expression, both when
  the flow saves and when it runs. The card flows must be turned off and on to pick up a changed value.
- Q9 is closed with this ADR. The cards themselves can't be edited after posting with the waiting action.

## Results (failure drills e and f, dev, 2026-11-01)

`scripts/failure-drills.ps1 -Drill ef`, with both durations set to `PT2M`. These three cards got no answer:
- RFP B's approval card (stage 8 rerun)
- a test gap on RFP S
- a test OCR flag on RFP D

| Drill | Result |
|---|---|
| e | PASS for all three flows. Each posted a reminder, then an escalation. Then the approval logged "Approval overdue", the OCR flag logged "Verification overdue" and stayed open, and the gap became Overdue |
| f | PASS. RFP B's next approval row was deleted while its card waited. When the waits ended, the approval flow found the row gone and posted the withdrawn notice (`Tell_Withdrawn` succeeded) |

The durations were reset to the `PT24H` default afterwards. How Teams presents a timed-out card stays unchecked
([UNVERIFIED]); it needs the interactive Teams sign-in noted in ADR-0025.
