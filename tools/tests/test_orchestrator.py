"""RFP Orchestrator tool answers (ADR-0030), on Dataverse-shaped rows. No gold data is read."""

from rfp_tools import orchestrator
from rfp_tools.schema import choice_value as C


def rfp(status="Awaiting human", stage=7):
    return {"hrs_rfpkey": "A", "hrs_stage": stage, "hrs_status": C("hrs_runstatus", status), "hrs_currentrevision": 0}


def test_run_status_names_the_waits_and_recent_log():
    out = orchestrator.run_status(rfp(), [{"hrs_name": "Stage 7 Gaps and clarifications", "hrs_status": C("hrs_stagestatus", "Succeeded"),
                                           "hrs_creditsestimate": 4.5, "hrs_errordetail": "Waiting for 2 SME answer(s)."}],
                                  [{"hrs_smestatus": C("hrs_smestatus", "Routed")}, {"hrs_smestatus": C("hrs_smestatus", "Answered")}], [], [])
    assert "Awaiting human" in out["text"] and "Waiting for 1 SME answer(s) of 2 gap(s)" in out["text"] and "4.5" in out["text"]
    assert orchestrator.run_status(None, [], [], [], [])["found"] is False


def test_run_status_keeps_past_waits_and_recent_credits_apart_from_current_state():
    """ADR-0035: a completed run's log row says it waited once; nothing should read as waiting now."""
    done = {**rfp(), "hrs_status": C("hrs_runstatus", "Completed")}
    log = [{"hrs_name": "Stage 7 Gaps and clarifications", "hrs_status": C("hrs_stagestatus", "Succeeded"), "hrs_creditsestimate": 4.5,
            "hrs_errordetail": "Waiting for 7 SME answer(s)."}]
    text = orchestrator.run_status(done, log, [{"hrs_smestatus": C("hrs_smestatus", "Answered")}], [], [])["text"]
    assert "Not waiting for anyone now." in text and "It paused then for 7 SME answer(s)." in text
    assert "Waiting for 7" not in text and "not the run's total" in text
    # Row details that aren't waits, such as a failure, are left as written.
    failed = [{**log[0], "hrs_errordetail": "G6: 21 pages, limit 20"}]
    assert "G6: 21 pages, limit 20" in orchestrator.run_status(done, failed, [], [], [])["text"]
    # A current wait still says so.
    waiting = orchestrator.run_status(rfp(), log, [{"hrs_smestatus": C("hrs_smestatus", "Routed")}], [], [])["text"]
    assert "Waiting for 1 SME answer(s)" in waiting and "Not waiting" not in waiting


def test_explain_flags_and_answer_carry_rule_position_and_answer_id():
    req = {"hrs_reqid": "CV-047", "hrs_location": "Section 4.9", "hrs_requirementtext": "Availability of 99.99%.",
           "hrs_compliance": C("hrs_compliance", "Partial")}
    flags = [{"hrs_flagtype": C("hrs_flagtype", "POLICY-RULE"), "hrs_severity": C("hrs_severity", "Warn"), "hrs_detail": "99.99 above 99.9",
              "hrs_policyrule": {"hrs_ruleid": "R-02", "hrs_approvedposition": "Commit to 99.9%."}}]
    text = orchestrator.explain_flags(req, flags)["text"]
    assert "POLICY-RULE [Warn] rule R-02" in text and "Commit to 99.9%" in text
    ans = orchestrator.explain_answer(req, [{"hrs_responsetext": "We commit to 99.9%.", "hrs_responsecode": "P",
                                             "hrs_answer": {"hrs_answercode": "ANS-0042", "hrs_question": "What is your SLA?"}}], [])["text"]
    assert "Compliance: Partial" in ans and "Answer ANS-0042" in ans


def test_rerun_check_refuses_running_filed_out_of_range_and_unreached():
    assert not orchestrator.rerun_check(rfp("Running"), 5)["allowed"]
    assert not orchestrator.rerun_check(rfp("Completed", 9), 5)["allowed"]
    assert not orchestrator.rerun_check(rfp(), 0)["allowed"] and not orchestrator.rerun_check(rfp(), "x")["allowed"]
    assert not orchestrator.rerun_check(rfp(stage=4), 7)["allowed"]
    ok = orchestrator.rerun_check(rfp("Failed", 3), "3")
    assert ok["allowed"] and ok["fromStage"] == 2 and ok["resetRevision"]
    assert orchestrator.rerun_check(rfp(), 5) == {"allowed": True, "fromStage": 5, "resetRevision": False, "reason": "Rerunning from stage 5."}
