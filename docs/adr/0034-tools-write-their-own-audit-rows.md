# ADR-0034 — Orchestrator tools write their own audit rows

- **Status:** Accepted
- **Date:** 2026-11-02
- **Deciders:** The user decided after the Teams findings in ADR-0033; solution architecture chose the details
- **Related:** [ADR-0030](0030-rfp-orchestrator-and-hooks.md), [ADR-0033](0033-reuse-sme-answers-and-teams-audit-length.md)

## Context

The audit record for Orchestrator tool calls came only from the Post tool use hook. Hooks are preview and fail silently
([Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agents-experience/hooks-overview), which also warns against
relying on a hook as the only safeguard for a business-critical rule). On 2026-11-02 in test, status questions in Teams ran the
tool and started no hook run, so those calls left no record.

## Options considered

- **Keep looking for why the hook didn't start.** It may be found, but a preview feature that fails silently would still be the
  only record.
- **Have the model pass the caller as a tool input.** The model could get it wrong or be talked into it, so it isn't evidence.
- **Read the request headers in each tool flow (chosen).** The platform sets them on every agent call.
- **Remove the Post tool use hook.** Its row has the caller's Entra object ID, which the tool row lacks in Teams. It stays.

## Decision

- Each of the four tool flows writes a `Tool run: <tool>` row to `hrs_runlog`, with the parameters, outcome, caller, client keywords
  (which name the channel) and conversation.
- **Read tools** write it first and still answer if the write fails.
- **Rerun Stage** writes it after its check and before changing anything, with "allowed" or "refused" and the reason. If the write
  fails, the flow stops and no rerun happens.
- Get Run Status leaves `Tool ...` rows out of its 8-row log, because audit rows were pushing stage rows out.

## Consequences

- **[UNVERIFIED]** Learn doesn't document the headers (`x-ms-object-id`, `x-ms-client-session-id`, `x-ms-client-keywords`). They were
  read from real runs in Teams and the test chat, and could change. A missing one is recorded as "unknown".
- **In Teams, the caller is the Teams user ID (`29:...`), not the Entra object ID.** It is specific to the agent. The test chat gives the
  Entra object ID. **Decided by the user, 2026-11-02:** in the pilot the caller is the admin account, so no mapping is built. Before anyone
  else uses the agent, this needs revisiting: the row alone wouldn't tell callers apart by name.
- A call can have two rows when the hook also fires: `Tool run:` from the tool and `Tool call:` from the hook. `Tool run:` is the
  one to count.
- A call the Pre tool use hook denies never reaches the tool, so it has only the hook's `Tool call denied:` row. If the hook fails,
  the tool's own check still refuses and records it.
- Each tool call adds one Dataverse action, and a row in `hrs_runlog`.

## Results (2026-11-02)

- **Dev, test chat:** all four tools wrote their rows. The rerun's row, with outcome "allowed", came before the rerun's own log row.
- **Test, Teams:** Get Run Status and Explain Answer wrote their rows, with channel `msteams` and the Teams user ID. The status log
  showed stage rows again.

Failure paths, tested in dev by turning a hook flow off and by making the audit write overflow its column at run time:

| Path | Result |
|---|---|
| Pre tool use hook down, rerun of a filed RFP | The agent continued, the tool refused and wrote "refused: ...", and the RFP didn't change |
| Audit write fails, Get Run Status | The tool still answered, with no `Tool run:` row. The flow run shows the failed write |
| Audit write fails, Rerun Stage on an allowed RFP | The flow stopped and the RFP didn't change. The user saw a generic 502 and a suggestion to retry, not the reason |
| Allowed rerun from Teams | `Tool run:` with "allowed", then the rerun's log row, then stage 8 ran |

- **The Teams user ID differs per agent:** the same person had a different `29:...` ID on the dev and test agents. It can't be compared across agents.

**Amendment 1 (2026-11-02).** That test showed the user a generic 502 and a suggestion to retry, which would fail the same way. Rerun
Stage now replies that nothing changed, that retrying won't help, and to ask an administrator. Retested in dev: the reply reached the
user and the RFP didn't change. The flow run still shows Failed, so the failure stays visible.

The same tests showed the agent refusing an RFP key outside "A, B, D", which came from its instructions and tool inputs. These no longer
list keys. The agent passes on the key the user gives, and the tool says if no RFP has it. An unknown key still writes a `Tool run:` row,
with no RFP link and the outcome "ran".
