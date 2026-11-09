# ADR-0035 — Status text separates current state from history, and the agent adds no claims of its own

- **Status:** Accepted
- **Date:** 2026-11-02
- **Deciders:** The user asked for both fixes before the pilot; solution architecture chose the details
- **Related:** [ADR-0030](0030-rfp-orchestrator-and-hooks.md), [ADR-0034](0034-tools-write-their-own-audit-rows.md)

## Context

Testing the Orchestrator found three wrong statements, on real replies.

1. It said a completed run's stage 7 was "still waiting" on SMEs. The run log row said "Waiting for 7 SME answer(s).", which was true
   when written.
2. It reported "Total Prompt Credits Used", but the number is the sum of the 8 log rows shown.
3. It said "RFP keys are case-sensitive". No tool said that, and it's false: Dataverse matched D and d, and requirement IDs the same way.

## Options considered

- **Instructions only.** This is the cheapest, but the first two come from text that invites the misreading, and instructions don't
  bind a model.
- **Change the stored run-log wording.** Other readers use those rows, and older rows would keep the old wording.
- **Change the text the tool composes (chosen for 1 and 2), and add instructions (for all three).** The composed text is
  deterministic and testable. It also covers rows already stored.

## Decision

- `run_status` always gives the current state: what it's waiting for, or "Not waiting for anyone now."
- It labels the log as history and its credit sum as not the run's total, and rewrites a row's "Waiting for ..." as "It paused then for ...".
- The not-found replies say that case makes no difference. An instruction alone didn't stop the claim: it came back in 2 of 4 replies.
- The agent's instructions say not to add causes, rules, limits or advice that a tool didn't state, to repeat a not-found sentence as
  given, and that waits come only from the current-state lines.

## Consequences

- **An invented claim is reduced, not prevented.** Nothing checks the model's wording after it writes. Replies can still add
  something, so a person should confirm from the run log before acting on an unexpected claim.
- The run's real total isn't available from this tool. The evaluation reports and the admin center remain the source for credits.
- The agent doesn't always repeat a not-found sentence exactly. It often adds "please try again" or rewords it, which is harmless.

## Results (2026-11-02)

- **Dev, test chat:** in 5 replies about completed runs, each said it wasn't waiting and described past waits as past. Asked twice for a total
  of credits, it said the tool doesn't give one. In 8 not-found replies, none claimed case-sensitivity.
- These are small samples of a model's wording, not a guarantee.
