"""Answers for the RFP Orchestrator's tools (ADR-0030): run status, why a flag was raised, why an answer was chosen,
and whether a stage may be rerun. The tool flows list Dataverse rows and pass them here; the text that comes back is
what the agent reads. Everything is composed from the rows, so the agent never needs company facts of its own.
"""

from collections.abc import Mapping, Sequence

from rfp_tools.schema import choice_label

STAGES = {1: "Normalize", 2: "Extract", 3: "Reconcile addendum", 4: "Analyze set", 5: "Select answers", 6: "Policy and drafting",
          7: "Gaps and clarifications", 8: "Assemble", 9: "Approval"}


# Stated outright: without it the agent told users that keys are case-sensitive, which is false (ADR-0035).
NO_RFP = "No RFP has that key. Upper or lower case makes no difference, so check the key's spelling."
NO_REQUIREMENT = "This RFP has no requirement with that ID. Upper or lower case makes no difference, so check the ID's spelling."


def _label(choice: str, value) -> str:
    try:
        return choice_label(choice, value) or ""
    except ValueError:
        return str(value)


def _expanded(row: Mapping, nav: str, column: str) -> str:
    return ((row or {}).get(nav) or {}).get(column) or ""


def run_status(rfp: Mapping | None, runlogs: Sequence[Mapping], gaps: Sequence[Mapping], ocr_open: Sequence[Mapping],
               approvals: Sequence[Mapping]) -> dict:
    """One RFP's position in the pipeline, what it is waiting for, and its latest run-log rows (newest first)."""
    if not rfp:
        return {"found": False, "text": NO_RFP}
    stage = int(rfp.get("hrs_stage") or 0)
    status = _label("hrs_runstatus", rfp.get("hrs_status"))
    lines = [f"RFP {rfp.get('hrs_rfpkey')}: {status}; last completed stage {stage}"
             + (f" ({STAGES[stage]})" if stage in STAGES else "") + f"; revision {rfp.get('hrs_currentrevision') or 0}."]
    routed = [g for g in gaps if _label("hrs_smestatus", g.get("hrs_smestatus")) == "Routed"]
    if routed:
        lines.append(f"Waiting for {len(routed)} SME answer(s) of {len(gaps)} gap(s).")
    if ocr_open:
        lines.append(f"Waiting for {len(ocr_open)} OCR verification(s).")
    pending = [a for a in approvals if _label("hrs_approvaldecision", a.get("hrs_decision")) == "Pending"]
    if pending:
        lines.append(f"Waiting for approval at level {pending[0].get('hrs_level')}.")
    if not (routed or ocr_open or pending):
        lines.append("Not waiting for anyone now.")
    credits = sum(float(r.get("hrs_creditsestimate") or 0) for r in runlogs)
    # The log is history. Worded so a wait it records isn't read as a current one, or its sum as the run's total (ADR-0035).
    lines.append(f"History, not current state: the last {len(runlogs)} run log row(s), which used {credits:.1f} prompt credits. "
                 "This is not the run's total.")
    for r in runlogs[:8]:
        detail = (r.get("hrs_errordetail") or "").strip()
        if detail.startswith("Waiting for "):
            detail = "It paused then for " + detail[len("Waiting for "):]
        lines.append(f"- {r.get('hrs_name')}: {_label('hrs_stagestatus', r.get('hrs_status'))}"
                     + (f", {float(r['hrs_creditsestimate']):.1f} credits" if r.get("hrs_creditsestimate") is not None else "")
                     + (f". {detail[:300]}" if detail else ""))
    return {"found": True, "stage": stage, "status": status, "text": "\n".join(lines)}


def explain_flags(requirement: Mapping | None, flags: Sequence[Mapping]) -> dict:
    """Every flag on one requirement: type, severity, the rule and its approved position, and the stated reason."""
    if not requirement:
        return {"found": False, "text": NO_REQUIREMENT}
    lines = [f"{requirement.get('hrs_reqid')} ({requirement.get('hrs_location') or 'no location'}): {requirement.get('hrs_requirementtext')}"]
    if not flags:
        lines.append("No flags.")
    for f in flags:
        rule = _expanded(f, "hrs_policyrule", "hrs_ruleid")
        position = _expanded(f, "hrs_policyrule", "hrs_approvedposition")
        lines.append(f"- {_label('hrs_flagtype', f.get('hrs_flagtype'))} [{_label('hrs_severity', f.get('hrs_severity'))}]"
                     + (f" rule {rule}" if rule else "") + f": {f.get('hrs_detail') or ''}"
                     + (f" Approved position: {position}" if position else "")
                     + (f" Resolved by {f['hrs_resolvedby']}." if f.get("hrs_resolvedby") else ""))
    return {"found": True, "count": len(flags), "text": "\n".join(lines)}


def explain_answer(requirement: Mapping | None, responses: Sequence[Mapping], gaps: Sequence[Mapping]) -> dict:
    """The compliance value, the approved library answer the response came from (rule 5: always its ID), the response
    text, and the gap if no approved answer existed."""
    if not requirement:
        return {"found": False, "text": NO_REQUIREMENT}
    lines = [f"{requirement.get('hrs_reqid')}: {requirement.get('hrs_requirementtext')}",
             f"Compliance: {_label('hrs_compliance', requirement.get('hrs_compliance')) or 'not derived yet'}."]
    for r in responses:
        code = _expanded(r, "hrs_answer", "hrs_answercode")
        lines.append(f"Answer {code or '(none)'}" + (f", approved for: {_expanded(r, 'hrs_answer', 'hrs_question')}" if code else "")
                     + (f" (last reviewed {_expanded(r, 'hrs_answer', 'hrs_lastreviewed')[:10]})" if _expanded(r, "hrs_answer", "hrs_lastreviewed") else "")
                     + ".")
        if r.get("hrs_responsecode"):
            lines.append(f"Response code: {r['hrs_responsecode']}.")
        if r.get("hrs_responsetext"):
            lines.append(f"Response: {r['hrs_responsetext'][:1500]}")
    for g in gaps:
        lines.append(f"Gap routed to {_expanded(g, 'hrs_routedtosme', 'hrs_name') or 'an SME'}: {_label('hrs_smestatus', g.get('hrs_smestatus'))}"
                     + (f". SME answer: {g['hrs_smeanswertext'][:800]}" if g.get("hrs_smeanswertext") else "."))
    if not responses and not gaps:
        lines.append("No response or gap yet.")
    return {"found": True, "text": "\n".join(lines)}


def rerun_check(rfp: Mapping | None, stage) -> dict:
    """Whether stage N may be rerun (ADR-0026, ADR-0030). Refused while the pipeline is running, once the RFP is filed,
    for a stage outside 1-9, or for a stage the RFP hasn't reached. Stage 3 reruns from stage 2. This is the control the
    Pre tool use hook duplicates; the hook can fail open, this check can't be skipped."""
    try:
        n = int(str(stage).strip())
    except (TypeError, ValueError):
        return {"allowed": False, "reason": f"Stage must be a number from 1 to 9, not {stage!r}."}
    if not rfp:
        return {"allowed": False, "reason": NO_RFP}
    status = _label("hrs_runstatus", rfp.get("hrs_status"))
    if not 1 <= n <= 9:
        return {"allowed": False, "reason": f"Stage must be from 1 to 9, not {n}."}
    if status == "Running":
        return {"allowed": False, "reason": "The pipeline is running for this RFP; wait for it to stop."}
    if status == "Completed":
        return {"allowed": False, "reason": "This RFP is approved and filed; it can't be rerun."}
    if n > int(rfp.get("hrs_stage") or 0) + 1:
        return {"allowed": False, "reason": f"The RFP has only completed stage {rfp.get('hrs_stage') or 0}; it can't rerun stage {n}."}
    from_stage = 2 if n == 3 else n
    return {"allowed": True, "fromStage": from_stage, "resetRevision": from_stage <= 2,
            "reason": f"Rerunning from stage {from_stage}" + (" (stage 3 reruns from stage 2)" if n == 3 else "") + "."}
