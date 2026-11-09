"""Generates solution/spec/flows/*.json - the RFP pipeline flows (ARCHITECTURE.md 3.4, ADR-0018).

    python tools/flowgen/build.py

Flows chain through Dataverse rather than child-flow calls:
  RFP - Receive Inbound Document  new file in Inbound -> hrs_rfp (status Running)
  RFP - Run Pipeline              hrs_rfp status -> Running: runs every stage after hrs_stage
  RFP - Route Gap To SME          hrs_gap created: card to the pilot recipient, waits, records the answer;
                                  when no gap is still Routed, sets the RFP back to Running
  RFP - Request Approval          hrs_approval created: approval card, waits; approve files the package,
                                  reject sends the run back to assembly
Logic lives in rfp-tools stage operations; these flows list rows, call, prompt, call and write.
"""

import json
import sys
from collections import OrderedDict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from flowgen import (  # noqa: E402
    Flow, append_variable, compose, condition, connector, dv_create, dv_create_from, dv_get, dv_list, dv_trigger,
    dv_delete, dv_update, env, env_inline, foreach, init_variable, prompt, prompt_output, scope, set_variable, sp_create_file,
    sp_get_content_by_path, teams_card_and_wait, teams_message, terminate, tools, word_to_pdf, SP,
)
from rfp_tools.schema import choice_value  # noqa: E402

OUT = HERE.parents[1] / "solution" / "spec" / "flows"
C = choice_value

RUNNING = C("hrs_runstatus", "Running")
AWAITING = C("hrs_runstatus", "Awaiting human")
FAILED = C("hrs_runstatus", "Failed")
COMPLETED = C("hrs_runstatus", "Completed")

RFP_ID = "@{outputs('Ids')?['rfpId']}"
DOC_ID = "@{outputs('Ids')?['documentId']}"
FLAG_COLUMNS = ["hrs_name", "hrs_flagtype", "hrs_severity", "hrs_detail", "hrs_rfp@odata.bind", "hrs_requirement@odata.bind"]


def body(action: str, *path: str) -> str:
    expr = f"body('{action}')"
    for p in path:
        expr += f"?['{p}']"
    return expr


def value(action: str) -> str:
    return f"body('{action}')?['value']"


def drive_relative(path_expr: str) -> str:
    """'/Shared Documents/Output/Draft/x.docx' -> '/Output/Draft/x.docx' (Word Online paths are drive-relative)."""
    return f"@{{concat('/', join(skip(split({path_expr}, '/'), 2), '/'))}}"


def gate(flow: Flow, container: OrderedDict, name: str, failed_expr: str, message_expr: str, stage_prefix: str) -> None:
    """If failed_expr is true: mark the RFP Failed with the message and stop the run (gates are code, 4.2)."""
    fail = OrderedDict()
    flow.add(fail, f"{name}_Mark_failed", dv_update("hrs_rfps", RFP_ID, {"hrs_status": FAILED}), after=None)
    flow.add(fail, f"{name}_Log", dv_update("hrs_runlogs", f"@{{outputs('{stage_prefix}_RunLog_Id')}}", {
        "hrs_status": C("hrs_stagestatus", "Failed"), "hrs_completedon": "@{utcNow()}", "hrs_errordetail": f"@{{{message_expr}}}",
        "hrs_creditsestimate": STAGE_CREDITS}))
    flow.add(fail, f"{name}_Stop", terminate("Failed", f"@{{{message_expr}}}"))
    flow.add(container, name, condition({"equals": [f"@{failed_expr}", True]}, fail, OrderedDict()))


def problems_gate(flow, container, name, action, stage_prefix):
    gate(flow, container, name, f"greater(length(coalesce({body(action, 'problems')}, json('[]'))), 0)",
         f"string({body(action, 'problems')})", stage_prefix)


# The stage's prompt credits so far: each prompt call appends AI Builder's costAsCopilotCredits to Usage, and the
# sum is taken with XPath over the array as XML (expressions have no sum()). ADR-0028.
STAGE_CREDITS = "@xpath(xml(json(concat('{\"r\":{\"v\":', string(variables('Usage')), '}}'))), 'sum(/r/v)')"


def _is_prompt(action: dict) -> bool:
    inputs = action.get("inputs")
    return isinstance(inputs, dict) and inputs.get("host", {}).get("operationId") == "aibuilderpredict_customprompt"


def instrument_prompts(container: OrderedDict) -> None:
    """After every prompt action, an action appending its credits to Usage; whatever ran after the prompt also
    waits for that append, so the stage's sum at the end includes every call."""
    for name in list(container):
        action = container[name]
        for child in (action.get("actions"), action.get("else", {}).get("actions")):
            if child:
                instrument_prompts(child)
        if _is_prompt(action):
            usage = f"{name}_Usage"
            container[usage] = {**append_variable("Usage", f"@coalesce(outputs('{name}')?['body/responsev2/predictionOutput']?['costAsCopilotCredits'], 0)"),
                                "runAfter": {name: ["Succeeded"]}}
            for other_name, other in container.items():
                if other_name != usage and name in other.get("runAfter", {}):
                    other["runAfter"][usage] = ["Succeeded"]


def stage(flow: Flow, number: int, title: str, build) -> str:
    """A stage runs only when hrs_stage is below its number; it logs to hrs_runlog and advances hrs_stage."""
    inner = OrderedDict()
    flow.add(inner, f"S{number}_RunLog", dv_create("hrs_runlogs", {
        "hrs_name": f"Stage {number} {title}", "hrs_rfp@odata.bind": f"hrs_rfps({RFP_ID})", "hrs_stage": number,
        "hrs_status": C("hrs_stagestatus", "Started"), "hrs_startedon": "@{utcNow()}",
        "hrs_correlationid": "@{workflow()?['run']?['name']}"}), after=None)
    flow.add(inner, f"S{number}_RunLog_Id", compose(f"@{body(f'S{number}_RunLog', 'hrs_runlogid')}"))
    flow.add(inner, f"S{number}_Usage_Reset", set_variable("Usage", []))
    if number >= 6:
        # Facts saved by stage 1 (limits, response codes, excluded sections) are not in the RFP row read at the
        # start of the run; later stages read a fresh copy and use it instead.
        flow.add(inner, f"S{number}_RFP", dv_get("hrs_rfps", RFP_ID))
        start = len(inner)
        build(inner, f"S{number}")
        for key in list(inner)[start:]:
            inner[key] = json.loads(json.dumps(inner[key]).replace("body('RFP')", f"body('S{number}_RFP')"))
    else:
        build(inner, f"S{number}")
    flow.add(inner, f"S{number}_Advance", dv_update("hrs_rfps", RFP_ID, {"hrs_stage": number}))
    flow.add(inner, f"S{number}_Done", dv_update("hrs_runlogs", f"@{{outputs('S{number}_RunLog_Id')}}", {
        "hrs_status": C("hrs_stagestatus", "Succeeded"), "hrs_completedon": "@{utcNow()}", "hrs_creditsestimate": STAGE_CREDITS}))
    instrument_prompts(inner)
    return f"Stage_{number}", condition({"less": ["@outputs('Ids')?['stage']", number]}, inner, OrderedDict())


# This RFP's Draft proposals from its own gap answers (ADR-0032, ADR-0033).
OWN_DRAFTS = f"startswith(hrs_answercode,'PROP-@{{outputs('Ids')?['key']}}-') and hrs_approvalstatus eq {C('hrs_approvalstatus', 'Draft')}"
OWN_DRAFTS_OUT = f"not ({OWN_DRAFTS})"


def conversation_key(meta: str) -> str:
    """hrs_correlationid holds 100 characters and a Teams conversation ID is longer, which failed the write. The audit
    text keeps the full ID (ADR-0033)."""
    return f"@{{take(coalesce({meta}?['conversationId'], ''), 100)}}"


def write_rows(flow, container, name, items_expr, entity_set, columns, concurrency=20):
    loop = OrderedDict()
    flow.add(loop, f"{name}_Create", dv_create_from(entity_set, f"items('{name}')", columns), after=None)
    flow.add(container, name, foreach(f"@{items_expr}", loop, concurrency))


def restart_for_addendum(flow: Flow, container: OrderedDict, prefix: str, rfp_action: str) -> None:
    """An addendum that arrived while the run was Running is recorded by the receive flow but not applied (ADR-0010).
    Before the run waits on people (stage 7) and when it ends (stage 9), a revision newer than the RFP's current one
    sends the run back to stage 3, which applies it and rebuilds stages 4-9 (ADR-0026's rerun, ADR-0010 amendment 1)."""
    flow.add(container, f"{prefix}_New_Revisions", dv_list("hrs_rfprevisions", "hrs_rfprevisionid",
                                                           f"{OF_RFP} and hrs_revisionnumber gt @{{coalesce({body(rfp_action, 'hrs_currentrevision')}, 0)}}"))
    restart = OrderedDict()
    flow.add(restart, f"{prefix}_Log_Addendum", dv_update("hrs_runlogs", f"@{{outputs('{prefix}_RunLog_Id')}}", {
        "hrs_status": C("hrs_stagestatus", "Succeeded"), "hrs_completedon": "@{utcNow()}", "hrs_creditsestimate": STAGE_CREDITS,
        "hrs_errordetail": "An addendum arrived during the run; restarting from stage 3 to apply it."}), after=None)
    flow.add(restart, f"{prefix}_Rewind_For_Addendum", dv_update("hrs_rfps", RFP_ID, {"hrs_stage": 2, "hrs_status": C("hrs_runstatus", "Not started")}))
    flow.add(restart, f"{prefix}_Restart_For_Addendum", dv_update("hrs_rfps", RFP_ID, {"hrs_status": RUNNING}))
    flow.add(restart, f"{prefix}_Stop_For_Addendum", terminate("Succeeded"))
    flow.add(container, f"{prefix}_If_New_Revision", condition({"greater": [f"@length({value(f'{prefix}_New_Revisions')})", 0]}, restart, OrderedDict()))


def batched_prompts(flow, container, name, batches_expr, calls: list[tuple[str, str, dict, str]], concurrency=4):
    """One loop over prompt-input batches (ADR-0029): each (action, prompt, inputs, variable) call runs per batch, in
    parallel with the others, and appends its output to the variable. `inputs` may use @{items('<name>')}."""
    loop = OrderedDict()
    for action, prompt_name, inputs, var in calls:
        flow.add(loop, action, prompt(prompt_name, inputs), after=None)
        flow.add(loop, f"Collect_{action}", append_variable(var, f"@{prompt_output(action)}"), after=action)
    flow.add(container, name, foreach(f"@{batches_expr}", loop, concurrency))


def update_rows(flow, container, name, items_expr, entity_set, fields: dict, concurrency=20, id_field="id"):
    loop = OrderedDict()
    flow.add(loop, f"{name}_Update", dv_update(entity_set, f"@items('{name}')?['{id_field}']", fields), after=None)
    flow.add(container, name, foreach(f"@{items_expr}", loop, concurrency))


REQ_SELECT = ("hrs_requirementid,hrs_reqid,hrs_section,hrs_location,hrs_requirementtext,hrs_status,hrs_mandatory,hrs_compliance,"
              "hrs_outofscope,hrs_ambiguous,hrs_contradictiongroup,_hrs_duplicateof_value")
REQ_EXPAND = "hrs_categorycode($select=hrs_categorycode)"
RESP_SELECT = "hrs_responseid,hrs_name,_hrs_requirement_value,hrs_smereviewrequired,hrs_responsetext,hrs_responsecode,hrs_wordcount,hrs_status"
RESP_EXPAND = "hrs_answer($select=hrs_answercode,hrs_question,hrs_answertext)"
RULE_SELECT = ("hrs_policyruleid,hrs_ruleid,hrs_triggerpattern,hrs_condition,hrs_severity,hrs_approvedposition,"
               "hrs_thresholdmetric,hrs_thresholdoperator,hrs_thresholdvalue")
RULE_EXPAND = "hrs_escalationsmeid($select=hrs_smecode)"
OF_RFP = f"_hrs_rfp_value eq {RFP_ID}"
VIA_REQ = f"hrs_requirement/_hrs_rfp_value eq {RFP_ID}"


def list_common(flow, c, prefix, *, flags=False, gaps=False, clarifications=False):
    flow.add(c, f"{prefix}_Reqs", dv_list("hrs_requirements", REQ_SELECT, OF_RFP, REQ_EXPAND, "hrs_reqid"))
    flow.add(c, f"{prefix}_Responses", dv_list("hrs_responses", RESP_SELECT, VIA_REQ, RESP_EXPAND))
    flow.add(c, f"{prefix}_Rules", dv_list("hrs_policyrules", RULE_SELECT, None, RULE_EXPAND))
    if flags:
        flow.add(c, f"{prefix}_Flags", dv_list("hrs_flags", "hrs_flagid,_hrs_requirement_value,hrs_flagtype,hrs_detail,_hrs_policyrule_value", OF_RFP))
    if gaps:
        flow.add(c, f"{prefix}_Gaps", dv_list("hrs_gaps", "hrs_gapid,_hrs_requirement_value,hrs_smeanswertext,hrs_smestatus", VIA_REQ))
    if clarifications:
        flow.add(c, f"{prefix}_Clarifications", dv_list("hrs_clarifications", "hrs_clarificationid,hrs_requirementids", OF_RFP))


# ==============================================================================================
def receive_flow() -> Flow:
    f = Flow("RFP - Receive Inbound Document", "New file in the Inbound folder -> hrs_rfp, revision 0 and document rows; starts the pipeline.", agent=True)
    trig = connector(SP, "GetOnNewFileItems", {"dataset": env("hrs_SharePointSiteUrl"), "table": env("hrs_DocumentLibraryId"),
                                                "folderPath": env("hrs_InboundLibraryUrl")})
    trig["recurrence"] = {"frequency": "Minute", "interval": 1}
    trig["splitOn"] = "@triggerOutputs()?['body/value']"
    f.trigger = {"When_a_file_is_created": trig}
    f._scan(trig)
    a = f.actions
    f.add(a, "File", compose({"name": "@triggerOutputs()?['body/{FilenameWithExtension}']",
                              "path": "@{concat('/', triggerOutputs()?['body/{FullPath}'])}"}), after=None)
    f.add(a, "Opportunities", dv_list("hrs_opportunities", "hrs_opportunityid,hrs_opportunitycode,hrs_rfpkey,hrs_rfpnumber,hrs_buyer"))
    f.add(a, "Opportunity_Shape", {"type": "Select", "inputs": {"from": f"@{value('Opportunities')}",
                                                                  "select": {"id": "@item()?['hrs_opportunityid']", "rfpNumber": "@item()?['hrs_rfpnumber']", "buyer": "@item()?['hrs_buyer']",
                                                                             "rfpKey": "@item()?['hrs_rfpkey']"}}})
    f.add(a, "Match", tools("MatchOpportunity", {"fileName": "@{outputs('File')?['name']}", "opportunities": f"@{body('Opportunity_Shape')}"}))
    stop = OrderedDict()
    f.add(stop, "No_match_stop", terminate("Failed", "@{concat('No opportunity matches ', outputs('File')?['name'])}"), after=None)
    f.add(a, "If_no_match", condition({"equals": [f"@{body('Match', 'opportunityId')}", None]}, stop, OrderedDict()))
    f.add(a, "Opportunity", dv_get("hrs_opportunities", f"@{body('Match', 'opportunityId')}", "hrs_rfpkey,hrs_duedate,hrs_qadeadline"))
    f.add(a, "Existing_RFP", dv_list("hrs_rfps", "hrs_rfpid,hrs_stage,hrs_status", f"hrs_rfpkey eq '@{{{body('Opportunity', 'hrs_rfpkey')}}}'"))
    exists = OrderedDict()
    # A file for an RFP already received is an addendum (ADR-0010): a new immutable revision with its own document.
    # Stage 3 applies it. If the pipeline is past stage 2 and not mid-run, it restarts from stage 3, which clears the
    # downstream state before applying the changes. Mid-run, the pipeline itself restarts at stage 7 or the end of
    # stage 9 when it finds the new revision (restart_for_addendum).
    existing = f"first({value('Existing_RFP')})"
    f.add(exists, "Existing_Revisions", dv_list("hrs_rfprevisions", "hrs_rfprevisionid", f"_hrs_rfp_value eq @{{{existing}?['hrs_rfpid']}}"),
          after=None)
    f.add(exists, "Create_Addendum_Revision", dv_create("hrs_rfprevisions", {
        "hrs_name": f"@{{concat({body('Opportunity', 'hrs_rfpkey')}, ' revision ', string(length({value('Existing_Revisions')})))}}",
        "hrs_rfp@odata.bind": f"hrs_rfps(@{{{existing}?['hrs_rfpid']}})",
        "hrs_revisionnumber": f"@length({value('Existing_Revisions')})", "hrs_effectivedate": "@{utcNow('yyyy-MM-dd')}",
        "hrs_sourcedocument": "@{outputs('File')?['path']}"}))
    f.add(exists, "Create_Addendum_Document", dv_create("hrs_rfpdocuments", {
        "hrs_name": "@{outputs('File')?['path']}",
        "hrs_rfprevision@odata.bind": f"hrs_rfprevisions(@{{{body('Create_Addendum_Revision', 'hrs_rfprevisionid')}}})",
        "hrs_format": f"@if(endsWith(toLower(outputs('File')?['name']), '.pdf'), {C('hrs_format', 'PdfNative')}, {C('hrs_format', 'Docx')})",
        "hrs_ocrused": False}))
    reprocess = OrderedDict()
    f.add(reprocess, "Reprocess_From_Stage_3", dv_update("hrs_rfps", f"@{{{existing}?['hrs_rfpid']}}", {"hrs_stage": 2, "hrs_status": RUNNING}),
          after=None)
    f.add(exists, "If_past_stage_2", condition({"and": [{"greater": [f"@int(coalesce({existing}?['hrs_stage'], 0))", 2]},
                                                        {"not": {"equals": [f"@{existing}?['hrs_status']", RUNNING]}}]}, reprocess, OrderedDict()))
    f.add(exists, "Addendum_recorded", terminate("Succeeded"))
    f.add(a, "If_exists", condition({"greater": [f"@length({value('Existing_RFP')})", 0]}, exists, OrderedDict()))
    f.add(a, "Create_RFP", dv_create("hrs_rfps", {
        "hrs_rfpkey": f"@{body('Opportunity', 'hrs_rfpkey')}", "hrs_opportunityid@odata.bind": f"hrs_opportunities(@{{{body('Match', 'opportunityId')}}})",
        "hrs_duedate": f"@{body('Opportunity', 'hrs_duedate')}", "hrs_qadeadline": f"@{body('Opportunity', 'hrs_qadeadline')}",
        "hrs_currentrevision": 0, "hrs_stage": 0, "hrs_status": C("hrs_runstatus", "Not started")}))
    f.add(a, "Create_Revision", dv_create("hrs_rfprevisions", {
        "hrs_name": f"@{{concat({body('Opportunity', 'hrs_rfpkey')}, ' revision 0')}}", "hrs_rfp@odata.bind": f"hrs_rfps(@{{{body('Create_RFP', 'hrs_rfpid')}}})",
        "hrs_revisionnumber": 0, "hrs_effectivedate": "@{utcNow('yyyy-MM-dd')}", "hrs_sourcedocument": "@{outputs('File')?['path']}"}))
    f.add(a, "Create_Document", dv_create("hrs_rfpdocuments", {
        "hrs_name": "@{outputs('File')?['path']}", "hrs_rfprevision@odata.bind": f"hrs_rfprevisions(@{{{body('Create_Revision', 'hrs_rfprevisionid')}}})",
        # Format from the extension; telling a scanned PDF from a native one arrives with OCR (Slice 5).
        "hrs_format": f"@if(endsWith(toLower(outputs('File')?['name']), '.pdf'), {C('hrs_format', 'PdfNative')}, {C('hrs_format', 'Docx')})",
        "hrs_ocrused": False}))
    f.add(a, "Start_Pipeline", dv_update("hrs_rfps", f"@{body('Create_RFP', 'hrs_rfpid')}", {"hrs_status": RUNNING}))
    return f


# ==============================================================================================
def pipeline_flow() -> Flow:
    f = Flow("RFP - Run Pipeline", "Runs every stage after hrs_stage when an RFP is set to Running (ADR-0001, ADR-0018).", agent=True)
    f.trigger = dv_trigger("hrs_rfp", 3, "hrs_status", [f"@equals(triggerOutputs()?['body/hrs_status'], {RUNNING})"], single_run=True)
    a = f.actions
    f.add(a, "Init_Sections", init_variable("Sections", "array", []), after=None)
    f.add(a, "Init_Usage", init_variable("Usage", "array", []))
    f.add(a, "Init_CompanyFiles", init_variable("CompanyFiles", "array", []))
    f.add(a, "Init_Drafted", init_variable("Drafted", "array", []))
    f.add(a, "Init_Requirements", init_variable("Requirements", "array", []))
    f.add(a, "Init_LowConfidence", init_variable("LowConfidence", "array", []))
    f.add(a, "Init_RevProblems", init_variable("RevProblems", "array", []))
    f.add(a, "Init_Shortlisted", init_variable("Shortlisted", "array", []))
    f.add(a, "Init_Selected", init_variable("Selected", "array", []))
    f.add(a, "Init_Selected_2", init_variable("Selected2", "array", []))
    for var in ("Classified", "Identified1", "Identified2", "Identified3", "Related1", "Related2", "Related3", "Screened", "Adjudicated",
                "Clarified"):
        f.add(a, f"Init_{var}", init_variable(var, "array", []))
    f.add(a, "RFP", dv_get("hrs_rfps", "@triggerOutputs()?['body/hrs_rfpid']"))
    not_running = OrderedDict()
    f.add(not_running, "Not_running_stop", terminate("Cancelled"), after=None)
    f.add(a, "If_not_running", condition({"not": {"equals": [f"@{body('RFP', 'hrs_status')}", RUNNING]}}, not_running, OrderedDict()))
    f.add(a, "Opportunity", dv_get("hrs_opportunities", f"@{body('RFP', '_hrs_opportunityid_value')}"))
    f.add(a, "Revision", dv_list("hrs_rfprevisions", "hrs_rfprevisionid,hrs_sourcedocument",
                                f"_hrs_rfp_value eq @{{{body('RFP', 'hrs_rfpid')}}} and hrs_revisionnumber eq 0"))
    f.add(a, "Document", dv_list("hrs_rfpdocuments", "hrs_rfpdocumentid,hrs_name,hrs_format",
                                f"_hrs_rfprevision_value eq @{{first({value('Revision')})?['hrs_rfprevisionid']}}"))
    f.add(a, "Ids", compose({
        "rfpId": f"@{body('RFP', 'hrs_rfpid')}", "documentId": f"@{{first({value('Document')})?['hrs_rfpdocumentid']}}",
        "path": f"@{{first({value('Revision')})?['hrs_sourcedocument']}}", "stage": f"@int(coalesce({body('RFP', 'hrs_stage')}, 0))",
        "key": f"@{body('RFP', 'hrs_rfpkey')}", "dueDate": f"@{body('RFP', 'hrs_duedate')}",
        "format": f"@first({value('Document')})?['hrs_format']",
        "stamp": "@{formatDateTime(utcNow(), 'yyyyMMdd-HHmmss')}",
        "draftFolder": f"@{{concat({env_inline('hrs_OutputLibraryUrl')}, '/Draft')}}"}))
    f.add(a, "Source_File", sp_get_content_by_path("@{outputs('Ids')?['path']}"))
    # Company content once per run: used by relations (scope), drafting and the narrative sections.
    f.add(a, "Company_File_List", connector(SP, "GetFileItems", {"dataset": env("hrs_SharePointSiteUrl"), "table": env("hrs_DocumentLibraryId"),
                                                                "folderPath": f"@{{concat({env_inline('hrs_ContentLibraryUrl')}, '/Company')}}"}))
    read = OrderedDict()
    f.add(read, "Company_File", sp_get_content_by_path("@{concat('/', items('Read_Company_Files')?['{FullPath}'])}"), after=None)
    f.add(read, "Collect_Company_File", append_variable("CompanyFiles", {"name": "@{items('Read_Company_Files')?['{FilenameWithExtension}']}",
                                                                           "contentBase64": "@{body('Company_File')?['$content']}"}))
    f.add(a, "Read_Company_Files", foreach(f"@{value('Company_File_List')}", read, 5))
    f.add(a, "Company", tools("CompanyContent", {"files": "@variables('CompanyFiles')"}))
    f.add(a, "Proposal_Template", sp_get_content_by_path(f"@{{concat({env_inline('hrs_ContentLibraryUrl')}, '/Templates/Proposal_Template.docx')}}"))

    # ---- Stage 1: normalize + RFP facts ----
    def s1(c, p):
        f.add(c, "Normalize", tools("NormalizeDocument", {"fileName": "@{outputs('Ids')?['path']}", "contentBase64": f"@{body('Source_File', '$content')}"}))
        gate(f, c, "Gate_G1", f"not({body('Normalize', 'gateG1', 'passed')})", f"string({body('Normalize', 'gateG1')})", p)
        f.add(c, "Block_Rows", tools("StageBlockRows", {"blocks": f"@{body('Normalize', 'blocks')}", "documentId": DOC_ID}))
        write_rows(f, c, "Write_Blocks", body("Block_Rows", "result"), "hrs_documentblocks",
                   ["hrs_name", "hrs_blockindex", "hrs_section", "hrs_blocktype", "hrs_text", "hrs_rowref", "hrs_confidence",
                    "hrs_pagenumber", "hrs_polygon", "hrs_rfpdocument@odata.bind"])
        f.add(c, "Headings", tools("TemplateHeadings", {"templateBase64": f"@{body('Proposal_Template', '$content')}"}))
        f.add(c, "Facts", prompt("RFP Extract RFP Facts", {"TemplateHeadings": f"@{{join({body('Headings', 'headings')}, decodeUriComponent('%0A'))}}",
                                                            "DocumentText": f"@{body('Normalize', 'factsText')}"}))
        f.add(c, "Facts_Row", tools("StageRfpFacts", {"facts": f"@{prompt_output('Facts')}", "opportunity": "@body('Opportunity')"}))
        f.add(c, "Save_Facts", dv_update("hrs_rfps", RFP_ID, {k: f"@{body('Facts_Row', k)}" for k in [
            "hrs_duedate", "hrs_qadeadline", "hrs_submissionmethod", "hrs_pagelimit", "hrs_pagelimitbasis", "hrs_wordlimitperanswer",
            "hrs_responsecodes", "hrs_evaluationweights", "hrs_requiredforms"]}))
        fmt = body("Normalize", "format")
        f.add(c, "Save_Document", dv_update("hrs_rfpdocuments", DOC_ID, {
            "hrs_format": f"@if(equals({fmt}, 'PdfScanned'), {C('hrs_format', 'PdfScanned')}, if(equals({fmt}, 'PdfNative'), "
                          f"{C('hrs_format', 'PdfNative')}, {C('hrs_format', 'Docx')}))",
            "hrs_ocrused": f"@{body('Normalize', 'ocrUsed')}", "hrs_meanconfidence": f"@{body('Normalize', 'meanConfidence')}",
            "hrs_pagecount": f"@{body('Normalize', 'pageCount')}"}))

    # ---- Stage 2: extract + classify ----
    def s2(c, p):
        rfp_number = f"@{body('Opportunity', 'hrs_rfpnumber')}"
        # Word: requirement tables with buyer IDs (code). Native PDF: code splits the prose into candidate
        # sentences, three parallel passes of RFP Identify Requirements mark the requirements, and code keeps
        # the majority and assigns IDs and locations (ADR-0022). Both branches leave the list in Requirements.
        docx = OrderedDict()
        f.add(docx, "Extract", tools("ExtractRequirements", {"contentBase64": f"@{body('Source_File', '$content')}", "rfpNumber": rfp_number,
                                                              "expectedMin": 1, "expectedMax": 1000}), after=None)
        gate(f, docx, "Gate_G2", f"not({body('Extract', 'gateG2', 'passed')})", f"string({body('Extract', 'gateG2')})", p)
        f.add(docx, "Set_Requirements_Docx", set_variable("Requirements", f"@{body('Extract', 'requirements')}"))
        # PDF, native or scanned. A document that leads its requirements with buyer IDs (RFP D) is extracted by code;
        # otherwise the prose path runs (ADR-0022 and its amendment). Either way the low-confidence list is kept
        # for the OCR flags written at the end of the stage (ADR-0025).
        pdf = OrderedDict()
        f.add(pdf, "Candidates", tools("RequirementCandidates", {"fileName": "@{outputs('Ids')?['path']}",
                                                                 "contentBase64": f"@{body('Source_File', '$content')}",
                                                                 "expectedMin": 1, "expectedMax": 1000}), after=None)
        labeled = OrderedDict()
        gate(f, labeled, "Gate_G2_Labeled", f"not({body('Candidates', 'gateG2', 'passed')})", f"string({body('Candidates', 'gateG2')})", p)
        labeled[next(iter(labeled))]["runAfter"] = {}
        f.add(labeled, "Set_Requirements_Labeled", set_variable("Requirements", f"@{body('Candidates', 'requirements')}"))
        f.add(labeled, "Set_Low_Labeled", set_variable("LowConfidence", f"@{body('Candidates', 'lowConfidence')}"))
        prose = OrderedDict()
        batched_prompts(f, prose, "Identify_Batches", body("Candidates", "candidateBatches"),
                        [(n, "RFP Identify Requirements", {"Candidates": "@{items('Identify_Batches')}"}, f"Identified{i}")
                         for i, n in enumerate(("Identify", "Identify_2", "Identify_3"), start=1)])
        prose["Identify_Batches"]["runAfter"] = {}
        f.add(prose, "Resolve", tools("ResolveRequirements", {"candidates": f"@{body('Candidates', 'candidates')}", "rfpNumber": rfp_number,
                                                               "passes": [f"@variables('Identified{i}')" for i in (1, 2, 3)],
                                                               "lowBlocks": f"@{body('Candidates', 'lowBlocks')}",
                                                               "expectedMin": 1, "expectedMax": 1000}))
        gate(f, prose, "Gate_G2_Pdf", f"not({body('Resolve', 'gateG2', 'passed')})", f"string({body('Resolve', 'gateG2')})", p)
        f.add(prose, "Set_Requirements_Pdf", set_variable("Requirements", f"@{body('Resolve', 'requirements')}"))
        f.add(prose, "Set_Low_Pdf", set_variable("LowConfidence", f"@{body('Resolve', 'lowConfidence')}"))
        f.add(pdf, "If_Labeled", condition({"equals": [f"@{body('Candidates', 'mode')}", "labeled"]}, labeled, prose))
        f.add(c, "Extract_By_Format", condition({"not": {"equals": ["@outputs('Ids')?['format']", C("hrs_format", "Docx")]}}, pdf, docx))
        f.add(c, "Categories_2", dv_list("hrs_categories", "hrs_categoryid,hrs_categorycode,hrs_name,hrs_description"))
        f.add(c, "Taxonomy", tools("StageTaxonomy", {"categories": f"@{value('Categories_2')}"}))
        f.add(c, "Classify_Inputs", tools("StageClassifyBatches", {"requirements": "@variables('Requirements')"}))
        batched_prompts(f, c, "Classify_Batches", body("Classify_Inputs", "batches"),
                        [("Classify", "RFP Classify Requirements", {"Taxonomy": f"@{body('Taxonomy', 'result')}",
                                                                    "Requirements": "@{items('Classify_Batches')}"}, "Classified")])
        f.add(c, "Requirement_Rows", tools("StageRequirementRows", {"rfpId": RFP_ID, "documentId": DOC_ID,
                                                                     "requirements": "@variables('Requirements')",
                                                                     "classification": "@variables('Classified')", "categories": f"@{value('Categories_2')}"}))
        problems_gate(f, c, "Gate_Classify", "Requirement_Rows", p)
        write_rows(f, c, "Write_Requirements", body("Requirement_Rows", "rows"), "hrs_requirements",
                   ["hrs_reqid", "hrs_rfp@odata.bind", "hrs_location", "hrs_section", "hrs_requirementtext", "hrs_originaltext",
                    "hrs_categorycode@odata.bind", "hrs_status", "hrs_extractionconfidence", "hrs_introducedinrevision", "hrs_outofscope",
                    "hrs_ambiguous", "hrs_sourceblock@odata.bind", "hrs_mandatory"])
        # OCR verification (ARCHITECTURE.md 6.3, ADR-0025): requirements read below the threshold get a flag, which
        # starts a verification card each. The run waits here, so unverified text never reaches selection or policy.
        f.add(c, "Reqs_2", dv_list("hrs_requirements", "hrs_requirementid,hrs_reqid", OF_RFP))
        f.add(c, "Ocr_Flags", tools("StageOcrFlags", {"rfpId": RFP_ID, "requirements": f"@{value('Reqs_2')}",
                                                       "lowConfidence": "@variables('LowConfidence')"}))
        verify = OrderedDict()
        # Await first, so a fast verification cannot find the run still marked Running.
        f.add(verify, "Await_Verification", dv_update("hrs_rfps", RFP_ID, {"hrs_status": AWAITING, "hrs_stage": 2}), after=None)
        write_rows(f, verify, "Write_Ocr_Flags", body("Ocr_Flags", "rows"), "hrs_flags", FLAG_COLUMNS)
        f.add(verify, "Log_Verifying", dv_update("hrs_runlogs", f"@{{outputs('{p}_RunLog_Id')}}", {
            "hrs_status": C("hrs_stagestatus", "Succeeded"), "hrs_completedon": "@{utcNow()}",
            "hrs_errordetail": f"@{{concat('Waiting for ', string({body('Ocr_Flags', 'count')}), ' OCR verification(s).')}}",
            "hrs_creditsestimate": STAGE_CREDITS}))
        f.add(verify, "Stop_until_verified", terminate("Succeeded"))
        f.add(c, "If_ocr", condition({"greater": [f"@{body('Ocr_Flags', 'count')}", 0]}, verify, OrderedDict()))

    # ---- Stage 3: addendum reconciliation (ADR-0010 and amendment 1) ----
    def s3(c, p):
        # A fresh copy of the RFP: stage 1 may have saved its facts earlier in this same run.
        f.add(c, "S3_RFP_Now", dv_get("hrs_rfps", RFP_ID))
        f.add(c, "Pending_Revisions", dv_list("hrs_rfprevisions", "hrs_rfprevisionid,hrs_revisionnumber,hrs_sourcedocument",
                                              f"_hrs_rfp_value eq {RFP_ID} and hrs_revisionnumber gt @{{coalesce({body('S3_RFP_Now', 'hrs_currentrevision')}, 0)}}",
                                              None, "hrs_revisionnumber"))
        R = "items('Apply_Revisions')"
        rev = OrderedDict()
        f.add(rev, "Rev_Document", dv_list("hrs_rfpdocuments", "hrs_rfpdocumentid", f"_hrs_rfprevision_value eq @{{{R}?['hrs_rfprevisionid']}}"),
              after=None)
        f.add(rev, "Rev_Doc_Id", compose(f"@{{first({value('Rev_Document')})?['hrs_rfpdocumentid']}}"))
        f.add(rev, "Rev_File", sp_get_content_by_path(f"@{{{R}?['hrs_sourcedocument']}}"))
        f.add(rev, "Rev_Normalize", tools("NormalizeDocument", {"fileName": f"@{{{R}?['hrs_sourcedocument']}}",
                                                                "contentBase64": f"@{body('Rev_File', '$content')}"}))
        f.add(rev, "Rev_Block_Rows", tools("StageBlockRows", {"blocks": f"@{body('Rev_Normalize', 'blocks')}", "documentId": "@{outputs('Rev_Doc_Id')}"}))
        write_rows(f, rev, "Write_Rev_Blocks", body("Rev_Block_Rows", "result"), "hrs_documentblocks",
                   ["hrs_name", "hrs_blockindex", "hrs_section", "hrs_blocktype", "hrs_text", "hrs_rowref", "hrs_confidence",
                    "hrs_pagenumber", "hrs_polygon", "hrs_rfpdocument@odata.bind"])
        f.add(rev, "Rev_Items", tools("AddendumItems", {"blocks": f"@{body('Rev_Normalize', 'blocks')}", "revision": f"@{R}?['hrs_revisionnumber']"}))
        f.add(rev, "Rev_Reqs", dv_list("hrs_requirements", "hrs_requirementid,hrs_reqid", OF_RFP))
        f.add(rev, "Rev_Ids", {"type": "Select", "inputs": {"from": f"@{value('Rev_Reqs')}", "select": "@item()?['hrs_reqid']"}})
        interpret = ("Interpret", "Interpret_2", "Interpret_3")
        for n in interpret:
            f.add(rev, n, prompt("RFP Interpret Addendum", {"RequirementIds": "@{join(body('Rev_Ids'), ', ')}",
                                                             "AddendumText": f"@{body('Rev_Items', 'text')}"}), after=["Rev_Ids"])
        f.add(rev, "Rev_Apply", tools("ApplyAddendum", {
            "rfpId": RFP_ID, "revision": f"@{R}?['hrs_revisionnumber']", "requirements": f"@{value('Rev_Reqs')}",
            "passes": [f"@{prompt_output(n)}" for n in interpret], "addendumText": f"@{body('Rev_Items', 'text')}",
            "labels": f"@{body('Rev_Items', 'labels')}", "locations": f"@{body('Rev_Items', 'locations')}",
            "labelBlocks": f"@{body('Rev_Items', 'labelBlocks')}", "requiredForms": f"@{body('S3_RFP_Now', 'hrs_requiredforms')}",
            "currentDueDate": f"@{body('S3_RFP_Now', 'hrs_duedate')}", "currentPageLimit": f"@{body('S3_RFP_Now', 'hrs_pagelimit')}"}), after=list(interpret))
        # Downstream state from an earlier pass is cleared; stages 4-9 rebuild it for the revised set (amendment 1:
        # full reprocessing, not selective). OCR and addendum flags stay.
        keep = " and ".join(f"hrs_flagtype ne {C('hrs_flagtype', t)}" for t in ("OCR-LOW-CONFIDENCE", "ADDENDUM-CHANGE", "ADDENDUM-WITHDRAWN"))
        for name, entity, key, filt in (("Old_Flags", "hrs_flags", "hrs_flagid", f"{OF_RFP} and {keep}"),
                                        ("Old_Responses", "hrs_responses", "hrs_responseid", VIA_REQ),
                                        ("Old_Gaps", "hrs_gaps", "hrs_gapid", VIA_REQ),
                                        ("Old_Clarifications", "hrs_clarifications", "hrs_clarificationid", OF_RFP),
                                        ("Old_Approvals", "hrs_approvals", "hrs_approvalid", OF_RFP)):
            f.add(rev, name, dv_list(entity, key, filt))
            loop = OrderedDict()
            f.add(loop, f"Delete_{name}_Row", dv_delete(entity, f"@items('Delete_{name}')?['{key}']"), after=None)
            f.add(rev, f"Delete_{name}", foreach(f"@{value(name)}", loop, 20))
        update_rows(f, rev, "Rev_Text_Updates", body("Rev_Apply", "textUpdates"), "hrs_requirements",
                    {"hrs_requirementtext": "@items('Rev_Text_Updates')?['value']"})
        update_rows(f, rev, "Rev_Status_Updates", body("Rev_Apply", "statusUpdates"), "hrs_requirements",
                    {"hrs_status": "@items('Rev_Status_Updates')?['value']"})
        update_rows(f, rev, "Rev_Mandatory_Updates", body("Rev_Apply", "mandatoryUpdates"), "hrs_requirements",
                    {"hrs_mandatory": "@items('Rev_Mandatory_Updates')?['value']"})
        write_rows(f, rev, "Write_Addendum_Flags", body("Rev_Apply", "requirementFlags"), "hrs_flags", FLAG_COLUMNS)
        write_rows(f, rev, "Write_Review_Flags", body("Rev_Apply", "reviewFlags"), "hrs_flags", FLAG_COLUMNS[:-1])
        added = OrderedDict()
        f.add(added, "Rev_Categories", dv_list("hrs_categories", "hrs_categoryid,hrs_categorycode,hrs_name,hrs_description"), after=None)
        f.add(added, "Rev_Taxonomy", tools("StageTaxonomy", {"categories": f"@{value('Rev_Categories')}"}))
        f.add(added, "Rev_Classify", prompt("RFP Classify Requirements", {"Taxonomy": f"@{body('Rev_Taxonomy', 'result')}",
                                                                           "Requirements": f"@{body('Rev_Apply', 'addedJson')}"}))
        f.add(added, "Rev_Requirement_Rows", tools("StageRequirementRows", {
            "rfpId": RFP_ID, "documentId": "@{outputs('Rev_Doc_Id')}", "requirements": f"@{body('Rev_Apply', 'added')}",
            "classification": f"@{prompt_output('Rev_Classify')}", "categories": f"@{value('Rev_Categories')}",
            "revision": f"@{R}?['hrs_revisionnumber']"}))
        # A Terminate cannot sit inside a loop, so problems are collected here and gated after it.
        collect = OrderedDict()
        f.add(collect, "Collect_Rev_Problems", append_variable("RevProblems", f"@{{string({body('Rev_Requirement_Rows', 'problems')})}}"), after=None)
        f.add(added, "If_Rev_Problems", condition({"greater": [f"@length(coalesce({body('Rev_Requirement_Rows', 'problems')}, json('[]')))", 0]},
                                                   collect, OrderedDict()))
        write_rows(f, added, "Write_Added_Requirements", body("Rev_Requirement_Rows", "rows"), "hrs_requirements",
                   ["hrs_reqid", "hrs_rfp@odata.bind", "hrs_location", "hrs_section", "hrs_requirementtext", "hrs_originaltext",
                    "hrs_categorycode@odata.bind", "hrs_status", "hrs_extractionconfidence", "hrs_introducedinrevision", "hrs_outofscope",
                    "hrs_ambiguous", "hrs_sourceblock@odata.bind", "hrs_mandatory"])
        f.add(added, "Added_Reqs", dv_list("hrs_requirements", "hrs_requirementid,hrs_reqid",
                                           f"{OF_RFP} and hrs_introducedinrevision eq @{{{R}?['hrs_revisionnumber']}}"))
        f.add(added, "Added_Flags", tools("StageAddedFlags", {"rfpId": RFP_ID, "revision": f"@{R}?['hrs_revisionnumber']",
                                                              "requirements": f"@{value('Added_Reqs')}", "added": f"@{body('Rev_Apply', 'added')}"}))
        write_rows(f, added, "Write_Added_Flags", body("Added_Flags", "rows"), "hrs_flags", FLAG_COLUMNS)
        f.add(rev, "If_added", condition({"greater": [f"@{body('Rev_Apply', 'addedCount')}", 0]}, added, OrderedDict()))
        f.add(rev, "Rev_Update_RFP", dv_update("hrs_rfps", RFP_ID, {k: f"@{body('Rev_Apply', 'rfp', k)}" for k in
                                                                     ("hrs_currentrevision", "hrs_duedate", "hrs_pagelimit", "hrs_requiredforms")}))
        f.add(c, "Apply_Revisions", foreach(f"@{value('Pending_Revisions')}", rev, 1))
        gate(f, c, "Gate_Rev_Classify", "greater(length(variables('RevProblems')), 0)", "string(variables('RevProblems'))", p)

    # ---- Stage 4: duplicates, contradictions, ambiguity, out of scope ----
    def s4(c, p):
        # Relations from an earlier pass (an addendum reprocess, a rerun) are cleared first, including the duplicate
        # lookup; only rows that carry one are touched (ADR-0026).
        f.add(c, "Related_4", dv_list("hrs_requirements", "hrs_requirementid", f"{OF_RFP} and (_hrs_duplicateof_value ne null or "
                                      "hrs_ambiguous eq true or hrs_outofscope eq true or hrs_contradictiongroup ne null)"))
        update_rows(f, c, "Clear_Relations", value("Related_4"), "hrs_requirements",
                    {"hrs_ambiguous": False, "hrs_outofscope": False, "hrs_contradictiongroup": None, "hrs_duplicateof@odata.bind": None},
                    id_field="hrs_requirementid")
        f.add(c, "Reqs_4", dv_list("hrs_requirements", REQ_SELECT, OF_RFP, REQ_EXPAND, "hrs_reqid"))
        f.add(c, "Relation_Inputs", tools("StageRelationBatches", {"requirements": f"@{value('Reqs_4')}"}))
        relation_inputs = {"ScopeStatement": f"@{body('Company', 'scope')}", "Requirements": "@{items('Relation_Batches')}"}
        # Three parallel passes per batch; findings at least two report are kept (self-consistency, ADR-0021 amendment 1).
        batched_prompts(f, c, "Relation_Batches", body("Relation_Inputs", "batches"),
                        [(n, "RFP Judge Relations", relation_inputs, f"Related{i}")
                         for i, n in enumerate(("Relations", "Relations_2", "Relations_3"), start=1)])
        f.add(c, "Relation_Rows", tools("StageRelationUpdates", {"rfpId": RFP_ID, "requirements": f"@{value('Reqs_4')}",
                                                                  "relations": "@variables('Related1')",
                                                                  "relations2": "@variables('Related2')",
                                                                  "relations3": "@variables('Related3')"}))
        problems_gate(f, c, "Gate_Relations", "Relation_Rows", p)
        update_rows(f, c, "Write_Duplicates", body("Relation_Rows", "duplicateUpdates"), "hrs_requirements",
                    {"hrs_duplicateof@odata.bind": "@items('Write_Duplicates')?['duplicateOf']"})
        update_rows(f, c, "Write_Attributes", body("Relation_Rows", "attributeUpdates"), "hrs_requirements",
                    {k: f"@items('Write_Attributes')?['{k}']" for k in ["hrs_contradictiongroup", "hrs_ambiguous", "hrs_outofscope"]})
        write_rows(f, c, "Write_Relation_Flags", body("Relation_Rows", "flags"), "hrs_flags", FLAG_COLUMNS)

    # ---- Stage 5: select approved answers ----
    def s5(c, p):
        f.add(c, "Reqs_5", dv_list("hrs_requirements", REQ_SELECT, OF_RFP, REQ_EXPAND, "hrs_reqid"))
        # This RFP's own Draft proposals are left out: they come from its own gap answers on an earlier pass, so flagging
        # them tells the reviewer nothing. Drafts from other RFPs still flag (ADR-0033).
        f.add(c, "Answers", dv_list("hrs_answers", "hrs_answerid,hrs_answercode,hrs_question,hrs_answertext,hrs_approvalstatus,hrs_lastreviewed,"
                                    "hrs_confidentiality,_hrs_supersededby_value", OWN_DRAFTS_OUT, "hrs_categorycode($select=hrs_categorycode)"))
        # Meaning-based shortlist over Approved questions in any category, then word overlap in the category (ADR-0019).
        f.add(c, "Shortlist_Items", tools("StageShortlistItems", {"requirements": f"@{value('Reqs_5')}", "answers": f"@{value('Answers')}"}))
        # Both prompts run in batches (one call over ~100 requirements comes back cut short); the outputs are
        # collected in variables and merged by rfp-tools.
        shortlist = OrderedDict()
        f.add(shortlist, "Shortlist", prompt("RFP Shortlist Answers", {"Index": f"@{body('Shortlist_Items', 'index')}",
                                                                        "Items": "@{items('Shortlist_Batches')}"}), after=None)
        f.add(shortlist, "Collect_Shortlist", append_variable("Shortlisted", f"@{prompt_output('Shortlist')}"))
        f.add(c, "Shortlist_Batches", foreach(f"@{body('Shortlist_Items', 'itemBatches')}", shortlist, 4))
        f.add(c, "Selection_Items", tools("StageSelectionItems", {"requirements": f"@{value('Reqs_5')}", "answers": f"@{value('Answers')}",
                                                                  "shortlist": "@variables('Shortlisted')"}))
        # Two passes per batch; an answer stands only when both find one (self-consistency, ADR-0021).
        select = OrderedDict()
        f.add(select, "Select_Answers", prompt("RFP Select Answers", {"Items": "@{items('Select_Batches')}"}), after=None)
        f.add(select, "Select_Answers_2", prompt("RFP Select Answers", {"Items": "@{items('Select_Batches')}"}), after=None)
        f.add(select, "Collect_Selected", append_variable("Selected", f"@{prompt_output('Select_Answers')}"), after=["Select_Answers"])
        f.add(select, "Collect_Selected_2", append_variable("Selected2", f"@{prompt_output('Select_Answers_2')}"), after=["Select_Answers_2"])
        f.add(c, "Select_Batches", foreach(f"@{body('Selection_Items', 'batches')}", select, 4))
        f.add(c, "Selections", tools("StageResolveSelections", {"rfpId": RFP_ID, "requirements": f"@{value('Reqs_5')}", "answers": f"@{value('Answers')}",
                                                                 "items": f"@{body('Selection_Items', 'items')}", "selections": "@variables('Selected')",
                                                                 "submissionDate": "@{outputs('Ids')?['dueDate']}",
                                                                 "shortlist": "@variables('Shortlisted')",
                                                                 "selections2": "@variables('Selected2')"}))
        problems_gate(f, c, "Gate_Selections", "Selections", p)
        write_rows(f, c, "Write_Responses", body("Selections", "responses"), "hrs_responses",
                   ["hrs_name", "hrs_requirement@odata.bind", "hrs_answer@odata.bind", "hrs_status", "hrs_smereviewrequired", "hrs_responsetext"])
        write_rows(f, c, "Write_Library_Flags", body("Selections", "flags"), "hrs_flags", FLAG_COLUMNS)

    # ---- Stage 6: policy, compliance, drafting ----
    def s6(c, p):
        list_common(f, c, "L6")
        f.add(c, "Certifications", dv_list("hrs_certifications", "hrs_certificationid,hrs_certid,hrs_name,hrs_issued,hrs_expires"))
        # Meaning-based rule screen adds candidates to the trigger-phrase matches (ADR-0019).
        f.add(c, "Rule_Screen_Items", tools("StageRuleScreenItems", {"requirements": f"@{value('L6_Reqs')}", "rules": f"@{value('L6_Rules')}"}))
        batched_prompts(f, c, "Rule_Screen_Batches", body("Rule_Screen_Items", "itemBatches"),
                        [("Rule_Screen", "RFP Screen Rules", {"Rules": f"@{body('Rule_Screen_Items', 'index')}",
                                                               "Items": "@{items('Rule_Screen_Batches')}"}, "Screened")])
        f.add(c, "Adjudication_Items", tools("StageAdjudicationItems", {"requirements": f"@{value('L6_Reqs')}", "rules": f"@{value('L6_Rules')}",
                                                                         "screen": "@variables('Screened')",
                                                                         "certifications": f"@{value('Certifications')}", "submissionDate": "@{outputs('Ids')?['dueDate']}"}))
        batched_prompts(f, c, "Adjudicate_Batches", body("Adjudication_Items", "batches"),
                        [("Adjudicate", "RFP Adjudicate Rules", {"Certifications": f"@{body('Adjudication_Items', 'certificationsText')}",
                                                                  "Items": "@{items('Adjudicate_Batches')}"}, "Adjudicated")])
        f.add(c, "Policy", tools("StageResolvePolicy", {
            "rfpId": RFP_ID, "requirements": f"@{value('L6_Reqs')}", "rules": f"@{value('L6_Rules')}", "certifications": f"@{value('Certifications')}",
            "responses": f"@{value('L6_Responses')}", "items": f"@{body('Adjudication_Items', 'items')}", "adjudications": "@variables('Adjudicated')",
            "mentions": f"@{body('Adjudication_Items', 'mentions')}",
            "buyerCodes": f"@coalesce(json(coalesce({body('RFP', 'hrs_pagelimitbasis')}, '{{}}'))?['responseCodes'], json('[]'))"}))
        problems_gate(f, c, "Gate_Policy", "Policy", p)
        write_rows(f, c, "Write_Policy_Flags", body("Policy", "policyFlags"), "hrs_flags", FLAG_COLUMNS + ["hrs_policyrule@odata.bind"])
        write_rows(f, c, "Write_Certificate_Flags", body("Policy", "certificationFlags"), "hrs_flags", FLAG_COLUMNS + ["hrs_certification@odata.bind"])
        update_rows(f, c, "Write_Compliance", body("Policy", "complianceUpdates"), "hrs_requirements",
                    {"hrs_compliance": "@items('Write_Compliance')?['fields']?['hrs_compliance']"})
        gate(f, c, "Gate_G4", f"not(equals(length({body('Policy', 'complianceUpdates')}), length({value('L6_Reqs')})))",
             "'G4: not every requirement received a compliance value'", p)
        batch = OrderedDict()
        f.add(batch, "Draft", prompt("RFP Draft Responses", {"WordLimit": f"@{{string(coalesce({body('RFP', 'hrs_wordlimitperanswer')}, 0))}}",
                                                              "CompanyContent": f"@{body('Company', 'text')}",
                                                              "Items": "@{items('Draft_Batches')}"}), after=None)
        f.add(batch, "Collect_Drafts", append_variable("Drafted", f"@{prompt_output('Draft')}"))
        f.add(c, "Draft_Batches", foreach(f"@{body('Policy', 'draftBatches')}", batch, 4))
        f.add(c, "Drafts", tools("StageResolveDrafts", {"draftItems": f"@{body('Policy', 'draftItems')}", "drafts": "@variables('Drafted')",
                                                         "wordLimit": f"@{body('RFP', 'hrs_wordlimitperanswer')}",
                                                         "countingRule": f"@{{if(greater(coalesce({body('RFP', 'hrs_wordlimitperanswer')}, 0), 0), 'excel_buyer_formula', 'whitespace')}}"}))
        problems_gate(f, c, "Gate_Drafts", "Drafts", p)
        update_rows(f, c, "Write_Drafts", body("Drafts", "updates"), "hrs_responses",
                    {k: f"@items('Write_Drafts')?['fields']?['{k}']" for k in ["hrs_responsetext", "hrs_responsecode", "hrs_wordcount", "hrs_status"]})

    # ---- Stage 7: gaps to SMEs, clarification letter ----
    def s7(c, p):
        restart_for_addendum(f, c, p, "RFP")
        list_common(f, c, "L7", flags=True)
        f.add(c, "Categories_7", dv_list("hrs_categories", "hrs_categoryid,hrs_categorycode", None, "hrs_primarysmeid($select=hrs_smecode)"))
        f.add(c, "Clarification_Items", tools("StageClarificationItems", {"requirements": f"@{value('L7_Reqs')}", "flags": f"@{value('L7_Flags')}"}))
        batched_prompts(f, c, "Clarify_Batches", body("Clarification_Items", "batches"),
                        [("Clarify", "RFP Draft Clarifications", {"BuyerName": f"@{body('Opportunity', 'hrs_buyer')}",
                                                                   "Items": "@{items('Clarify_Batches')}"}, "Clarified")])
        f.add(c, "Clarifications", tools("StageResolveClarifications", {"rfpId": RFP_ID, "items": f"@{body('Clarification_Items', 'items')}",
                                                                         "drafted": "@variables('Clarified')"}))
        problems_gate(f, c, "Gate_Clarifications", "Clarifications", p)
        write_rows(f, c, "Write_Clarifications", body("Clarifications", "rows"), "hrs_clarifications",
                   ["hrs_name", "hrs_rfp@odata.bind", "hrs_requirementids", "hrs_trigger", "hrs_questiontext", "hrs_status"])
        f.add(c, "Letter_Template", sp_get_content_by_path(f"@{{concat({env_inline('hrs_ContentLibraryUrl')}, '/Templates/Clarification_Letter_Template.docx')}}"))
        f.add(c, "Letter", tools("FillTemplate", {"templateBase64": f"@{body('Letter_Template', '$content')}", "scalars": {
            "SubmissionDate": "@{utcNow('MMMM d, yyyy')}", "BuyerContactName": "Procurement Office", "BuyerContactTitle": "",
            "BuyerName": f"@{body('Opportunity', 'hrs_buyer')}", "BuyerContactEmail": "", "RFPNumber": f"@{body('Opportunity', 'hrs_rfpnumber')}",
            "RFPTitle": f"@{body('Opportunity', 'hrs_title')}", "QADeadline": f"@{body('RFP', 'hrs_qadeadline')}"},
            "repeatingRows": {"n": f"@{body('Clarifications', 'letterRows')}"}}))
        f.add(c, "Save_Letter", sp_create_file("@{outputs('Ids')?['draftFolder']}",
                                               "@{concat(outputs('Ids')?['key'], '-', outputs('Ids')?['stamp'], '-Clarification-Letter.docx')}",
                                               f"@base64ToBinary({body('Letter', 'contentBase64')})"))
        f.add(c, "Notify_Letter", teams_message(env("hrs_PilotRecipientUpn"),
                                                f"@{{concat('<p>Clarification letter for ', {body('Opportunity', 'hrs_rfpnumber')}, ' is ready for review: ', outputs('Ids')?['draftFolder'], '/', outputs('Ids')?['key'], '-', outputs('Ids')?['stamp'], '-Clarification-Letter.docx. Q&amp;A deadline: ', {body('RFP', 'hrs_qadeadline')}, '.</p>')}}"))
        f.add(c, "Proposals_7", dv_list("hrs_answers", "hrs_answerid,hrs_answercode,hrs_question,hrs_answertext", OWN_DRAFTS))
        f.add(c, "Gaps", tools("StageGapRows", {"requirements": f"@{value('L7_Reqs')}", "responses": f"@{value('L7_Responses')}", "flags": f"@{value('L7_Flags')}",
                                                 "rules": f"@{value('L7_Rules')}", "categories": f"@{value('Categories_7')}",
                                                 "dueBy": "@{addDays(utcNow(), 2)}", "rfpId": RFP_ID,
                                                 "proposals": f"@{value('Proposals_7')}", "rfpKey": "@{outputs('Ids')?['key']}"}))
        write_rows(f, c, "Write_Gap_Flags", body("Gaps", "flags"), "hrs_flags", FLAG_COLUMNS)
        # An answer to unchanged text carries over with no card (ADR-0033). Written before the wait, so the last card's
        # check for gaps still Routed can't race these rows.
        reused_loop = OrderedDict()
        f.add(reused_loop, "Create_Reused_Gap", dv_create("hrs_gaps", {
            **{k: f"@{{items('Write_Reused_Gaps')?['{k}']}}" for k in ("hrs_name", "hrs_requirement@odata.bind", "hrs_routedtosme@odata.bind",
                                                                       "hrs_dueby", "hrs_smeanswertext", "hrs_proposedanswer@odata.bind")},
            "hrs_routedon": "@{utcNow()}", "hrs_smestatus": "@items('Write_Reused_Gaps')?['hrs_smestatus']"}), after=None)
        f.add(c, "Write_Reused_Gaps", foreach(f"@{body('Gaps', 'reusedRows')}", reused_loop, 10))
        # Await the SMEs before creating gaps, so a fast answer cannot be overwritten by this update.
        wait = OrderedDict()
        f.add(wait, "Await_SMEs", dv_update("hrs_rfps", RFP_ID, {"hrs_status": AWAITING, "hrs_stage": 7}), after=None)
        gap_loop = OrderedDict()
        f.add(gap_loop, "Create_Gap", dv_create("hrs_gaps", {
            "hrs_name": "@{items('Write_Gaps')?['hrs_name']}", "hrs_requirement@odata.bind": "@{items('Write_Gaps')?['hrs_requirement@odata.bind']}",
            "hrs_routedtosme@odata.bind": "@{items('Write_Gaps')?['hrs_routedtosme@odata.bind']}", "hrs_routedon": "@{utcNow()}",
            "hrs_dueby": "@{items('Write_Gaps')?['hrs_dueby']}", "hrs_smestatus": "@items('Write_Gaps')?['hrs_smestatus']"}), after=None)
        f.add(wait, "Write_Gaps", foreach(f"@{body('Gaps', 'rows')}", gap_loop, 10))
        f.add(wait, "Log_Waiting", dv_update("hrs_runlogs", f"@{{outputs('{p}_RunLog_Id')}}", {
            "hrs_status": C("hrs_stagestatus", "Succeeded"), "hrs_completedon": "@{utcNow()}",
            "hrs_errordetail": f"@{{concat('Waiting for ', string({body('Gaps', 'count')}), ' SME answer(s).')}}",
            "hrs_creditsestimate": STAGE_CREDITS}))
        f.add(wait, "Stop_until_answered", terminate("Succeeded"))
        f.add(c, "If_gaps", condition({"greater": [f"@{body('Gaps', 'count')}", 0]}, wait, OrderedDict()))

    # ---- Stage 8: narrative, package, page count, gates G5/G6 ----
    def s8(c, p):
        list_common(f, c, "L8", flags=True, gaps=True)
        f.add(c, "Section_Inputs", tools("StageSectionInputs", {"requirements": f"@{value('L8_Reqs')}", "responses": f"@{value('L8_Responses')}",
                                                                 "opportunity": "@body('Opportunity')", "budgetScale": 1}))
        sec = OrderedDict()
        f.add(sec, "Draft_Section", prompt("RFP Draft Section", {
            "SectionName": "@{items('Write_Sections')?['sectionName']}", "Guidance": "@{items('Write_Sections')?['guidance']}",
            "WordBudget": "@{items('Write_Sections')?['wordBudget']}", "BuyerContext": "@{items('Write_Sections')?['buyerContext']}",
            "Responses": "@{items('Write_Sections')?['responses']}", "CompanyContent": f"@{body('Company', 'text')}"}), after=None)
        f.add(sec, "Collect_Section", append_variable("Sections", {"token": "@{items('Write_Sections')?['token']}",
                                                                    "text": f"@{{{prompt_output('Draft_Section')}?['text']}}"}))
        f.add(c, "Write_Sections", foreach(f"@{body('Section_Inputs', 'result')}", sec, 5))
        f.add(c, "Team", dv_list("hrs_teammembers", "hrs_name,hrs_role,hrs_yearsexperience,hrs_certifications", None, None, "hrs_staffid"))
        f.add(c, "Team_Rows", {"type": "Select", "inputs": {"from": f"@{value('Team')}", "select": {
            "Name": "@item()?['hrs_name']", "Role": "@item()?['hrs_role']", "Years": "@{item()?['hrs_yearsexperience']}",
            "Certifications": "@item()?['hrs_certifications']"}}})
        f.add(c, "Signatory", dv_list("hrs_smes", "hrs_name,hrs_title", f"hrs_name eq '@{{{body('Opportunity', 'hrs_bidowner')}}}'"))
        f.add(c, "Package_Rows", tools("StagePackageRequirements", {"requirements": f"@{value('L8_Reqs')}", "responses": f"@{value('L8_Responses')}",
                                                                     "flags": f"@{value('L8_Flags')}", "rules": f"@{value('L8_Rules')}", "gaps": f"@{value('L8_Gaps')}"}))
        f.add(c, "Matrix_Template", sp_get_content_by_path(f"@{{concat({env_inline('hrs_ContentLibraryUrl')}, '/Templates/Compliance_Matrix_Template.xlsx')}}"))
        f.add(c, "Package", tools("AssemblePackage", {
            "proposalTemplateBase64": f"@{body('Proposal_Template', '$content')}", "matrixTemplateBase64": f"@{body('Matrix_Template', '$content')}",
            "scalars": {"RFPNumber": f"@{body('Opportunity', 'hrs_rfpnumber')}", "RFPTitle": f"@{body('Opportunity', 'hrs_title')}",
                        "BuyerName": f"@{body('Opportunity', 'hrs_buyer')}", "SubmissionDate": f"@{body('RFP', 'hrs_duedate')}",
                        "SignatoryName": f"@{body('Opportunity', 'hrs_bidowner')}",
                        "SignatoryTitle": f"@{{coalesce(first({value('Signatory')})?['hrs_title'], '')}}", "Company": env("hrs_CompanyName")},
            "sections": "@variables('Sections')", "requirements": f"@{body('Package_Rows', 'result')}", "team": "@body('Team_Rows')",
            "excludedHeadings": f"@coalesce(json(coalesce({body('RFP', 'hrs_pagelimitbasis')}, '{{}}'))?['excludedTemplateSections'], json('[]'))",
            "requiredForms": f"@json(coalesce({body('RFP', 'hrs_requiredforms')}, '[]'))"}))
        names = {"Narrative": "docx", "Excluded": "docx", "Compliance-Matrix": "xlsx"}
        sources = {"Narrative": "governedBase64", "Excluded": "excludedBase64", "Compliance-Matrix": "matrixBase64"}
        for label, ext in names.items():
            f.add(c, f"Save_{label.replace('-', '_')}", sp_create_file(
                "@{outputs('Ids')?['draftFolder']}", f"@{{concat(outputs('Ids')?['key'], '-', outputs('Ids')?['stamp'], '-{label}.{ext}')}}",
                f"@base64ToBinary({body('Package', sources[label])})"))
        f.add(c, "Files", compose({label: f"@{{concat(outputs('Ids')?['draftFolder'], '/', outputs('Ids')?['key'], '-', outputs('Ids')?['stamp'], '-{label}.{ext}')}}"
                                   for label, ext in names.items()}))
        f.add(c, "Narrative_PDF", word_to_pdf(drive_relative("outputs('Files')?['Narrative']")))
        f.add(c, "Pages", tools("CountPages", {"pdfBase64": f"@{body('Narrative_PDF', '$content')}"}))
        f.add(c, "Gate_Inputs", tools("StageGateInputs", {"packageRows": f"@{body('Package_Rows', 'result')}", "responses": f"@{value('L8_Responses')}",
                                                           "wordLimit": f"@{body('RFP', 'hrs_wordlimitperanswer')}"}))
        f.add(c, "Ocr_Open", dv_list("hrs_flags", "hrs_name",
                                     f"{OF_RFP} and hrs_flagtype eq {C('hrs_flagtype', 'OCR-LOW-CONFIDENCE')} and hrs_resolvedon eq null"))
        f.add(c, "Ocr_Open_Names", {"type": "Select", "inputs": {"from": f"@{value('Ocr_Open')}", "select": "@item()?['hrs_name']"}})
        f.add(c, "Gates", tools("CheckGates", {
            "g5": {"flags": f"@{body('Gate_Inputs', 'g5Flags')}", "exceptionKeys": f"@{body('Package', 'exceptionKeys')}"},
            "g6": {"pageCount": f"@{body('Pages', 'pages')}", "pageLimit": f"@{body('RFP', 'hrs_pagelimit')}",
                   "wordCounts": f"@{body('Gate_Inputs', 'wordCounts')}", "wordLimit": f"@{body('Gate_Inputs', 'wordLimit')}",
                   # Every required form must be on the package's checklist, as Included or Attach before submission;
                   # the RFP row is read again here, so a form an addendum added after assembly is caught.
                   "requiredForms": f"@json(coalesce({body('RFP', 'hrs_requiredforms')}, '[]'))",
                   "providedForms": f"@{body('Package', 'accountedForms')}", "unresolvedOcr": "@body('Ocr_Open_Names')"}}))
        breach = OrderedDict()
        f.add(breach, "Limit_Breach_Flag", dv_create("hrs_flags", {
            "hrs_name": "LIMIT-BREACH package", "hrs_flagtype": C("hrs_flagtype", "LIMIT-BREACH"), "hrs_severity": C("hrs_severity", "Block"),
            "hrs_detail": f"@{{string({body('Gates', 'gates')})}}", "hrs_rfp@odata.bind": f"hrs_rfps({RFP_ID})"}), after=None)
        f.add(c, "If_gates_fail", condition({"equals": [f"@{body('Gates', 'passed')}", False]}, breach, OrderedDict()))
        gate(f, c, "Gate_G5_G6", f"not({body('Gates', 'passed')})", f"string({body('Gates', 'gates')})", p)
        f.add(c, "Package_Facts", compose({"files": "@outputs('Files')", "pages": f"@{body('Pages', 'pages')}"}))
        # Saved on the row because stage 9 can start in a later flow run than stage 8: a rerun, a rewind or the retry (ADR-0032).
        f.add(c, "Save_Package_Facts", dv_update("hrs_rfps", RFP_ID, {"hrs_packagefacts": "@{string(outputs('Package_Facts'))}"}))

    # ---- Stage 9: approval level and the single approval request ----
    def s9(c, p):
        gate(f, c, "Gate_Package_Facts", f"empty({body('RFP', 'hrs_packagefacts')})",
             "'No saved package from stage 8. Rerun from stage 8.'", p)
        f.add(c, "Package_Facts_9", compose(f"@json({body('RFP', 'hrs_packagefacts')})"))
        list_common(f, c, "L9", flags=True, gaps=True, clarifications=True)
        f.add(c, "Package_Rows_9", tools("StagePackageRequirements", {"requirements": f"@{value('L9_Reqs')}", "responses": f"@{value('L9_Responses')}",
                                                                       "flags": f"@{value('L9_Flags')}", "rules": f"@{value('L9_Rules')}", "gaps": f"@{value('L9_Gaps')}"}))
        f.add(c, "Summary", tools("StageApprovalSummary", {"requirements": f"@{value('L9_Reqs')}", "gaps": f"@{value('L9_Gaps')}",
                                                            "clarifications": f"@{value('L9_Clarifications')}", "packageRows": f"@{body('Package_Rows_9', 'result')}"}))
        f.add(c, "Level", tools("DeriveApprovalLevel", {"valueBand": f"@{body('Opportunity', 'hrs_valueband')}",
                                                         "flagSeverities": f"@{body('Summary', 'flagSeverities')}"}))
        f.add(c, "Approver", dv_list("hrs_approvers", "hrs_approverid,hrs_level,hrs_approvername", f"hrs_level eq '@{{{body('Level', 'approvalLevel')}}}'"))
        f.add(c, "Card_Template", sp_get_content_by_path(f"@{{concat({env_inline('hrs_ContentLibraryUrl')}, '/Templates/Approval_Card.json')}}"))
        f.add(c, "Card", tools("RenderCard", {"template": f"@{{base64ToString({body('Card_Template', '$content')})}}", "data": {
            "buyerName": f"@{body('Opportunity', 'hrs_buyer')}", "rfpNumber": f"@{body('Opportunity', 'hrs_rfpnumber')}",
            "dueDate": f"@{body('RFP', 'hrs_duedate')}", "valueBand": f"@{body('Opportunity', 'hrs_valueband')}",
            "approvalLevel": f"@{{concat({body('Level', 'approvalLevel')}, ' - ', first({value('Approver')})?['hrs_approvername'])}}",
            "totalRequirements": f"@{body('Summary', 'totalRequirements')}", "countFull": f"@{body('Summary', 'countFull')}",
            "countPartial": f"@{body('Summary', 'countPartial')}", "countNo": f"@{body('Summary', 'countNo')}",
            "countGap": f"@{body('Summary', 'countGap')}", "countClarify": f"@{body('Summary', 'countClarify')}",
            "pageCount": "@{outputs('Package_Facts_9')?['pages']}", "pageLimit": f"@{body('RFP', 'hrs_pagelimit')}",
            "draftUrl": f"@{{concat({env_inline('hrs_SharePointSiteUrl')}, outputs('Package_Facts_9')?['files']?['Narrative'])}}",
            "exceptions": f"@{body('Summary', 'exceptions')}"}}))
        f.add(c, "Request_Approval", dv_create("hrs_approvals", {
            "hrs_name": f"@{{concat('Approval ', {body('Opportunity', 'hrs_rfpnumber')})}}", "hrs_rfp@odata.bind": f"hrs_rfps({RFP_ID})",
            "hrs_approver@odata.bind": f"hrs_approvers(@{{first({value('Approver')})?['hrs_approverid']}})",
            "hrs_level": f"@{body('Level', 'approvalLevel')}", "hrs_decision": C("hrs_approvaldecision", "Pending"), "hrs_requestedon": "@{utcNow()}",
            "hrs_cardpayload": f"@{{string(setProperty(setProperty(json('{{}}'), 'card', {body('Card', 'card')}), 'files', outputs('Package_Facts_9')?['files']))}}",
            "hrs_totalrequirements": f"@{body('Summary', 'totalRequirements')}", "hrs_fullcount": f"@{body('Summary', 'countFull')}",
            "hrs_partialcount": f"@{body('Summary', 'countPartial')}", "hrs_nocount": f"@{body('Summary', 'countNo')}",
            "hrs_gapsrouted": f"@{body('Summary', 'gapsRouted')}", "hrs_clarificationssent": f"@{body('Summary', 'clarificationsSent')}",
            "hrs_pagecount": "@{outputs('Package_Facts_9')?['pages']}"}))
        f.add(c, "Await_Approval", dv_update("hrs_rfps", RFP_ID, {"hrs_status": AWAITING}))
        restart_for_addendum(f, c, p, "RFP")

    previous = "Proposal_Template"
    for number, title, build in [(1, "Normalize", s1), (2, "Extract", s2), (3, "Reconcile addendum", s3), (4, "Analyze set", s4),
                                 (5, "Select answers", s5), (6, "Policy and drafting", s6), (7, "Gaps and clarifications", s7),
                                 (8, "Assemble", s8), (9, "Approval", s9)]:
        name, action = stage(f, number, title, build)

        f.add(a, name, action, after=previous)
        previous = name
    f.add(a, "On_failure", scope(on_failure_actions(f)), after=previous)
    a["On_failure"]["runAfter"] = {previous: ["Failed", "Skipped", "TimedOut"]}
    return f


# Rows a stage creates for one RFP, dependents first. Removing those created since a point in time undoes every
# stage that started after it (ADR-0026): the failure handler passes the failed attempt's start, a rerun from
# stage N passes the start of stage N's latest pass. Updates (compliance, drafts, revised text) are overwritten
# when the stage runs again; stage 4 clears the relation columns itself.
TRIGGER_RFP = "@{triggerOutputs()?['body/hrs_rfpid']}"
TRIGGER_KEY = "@{triggerOutputs()?['body/hrs_rfpkey']}"


def created_since_tables(rfp: str, key: str) -> list[tuple[str, str, str]]:
    return [("hrs_approvals", "hrs_approvalid", f"_hrs_rfp_value eq {rfp}"),
            ("hrs_clarifications", "hrs_clarificationid", f"_hrs_rfp_value eq {rfp}"),
            ("hrs_gaps", "hrs_gapid", f"hrs_requirement/_hrs_rfp_value eq {rfp}"),
            ("hrs_responses", "hrs_responseid", f"hrs_requirement/_hrs_rfp_value eq {rfp}"),
            ("hrs_flags", "hrs_flagid", f"_hrs_rfp_value eq {rfp}"),
            ("hrs_requirements", "hrs_requirementid", f"_hrs_rfp_value eq {rfp}"),
            ("hrs_documentblocks", "hrs_documentblockid", f"hrs_rfpdocument/hrs_rfprevision/_hrs_rfp_value eq {rfp}"),
            ("hrs_answers", "hrs_answerid", f"startswith(hrs_answercode,'PROP-{key}-') and hrs_approvalstatus eq {C('hrs_approvalstatus', 'Draft')}")]


def delete_created_since(flow: Flow, container: OrderedDict, prefix: str, since_expr: str, rfp: str, key: str) -> None:
    for entity, id_col, filt in created_since_tables(rfp, key):
        name = f"{prefix}_{entity.removeprefix('hrs_').title()}"
        flow.add(container, name, dv_list(entity, id_col, f"{filt} and createdon ge {since_expr}"))
        loop = OrderedDict()
        flow.add(loop, f"{name}_Delete", dv_delete(entity, f"@items('{name}_Each')?['{id_col}']"), after=None)
        flow.add(container, f"{name}_Each", foreach(f"@{value(name)}", loop, 20))


def on_failure_actions(f: Flow) -> OrderedDict:
    """An unexpected failure inside stage N (not a gate: gates terminate the run themselves and never retry). The
    rows the failed attempt created are removed, the stage's run-log row is marked Failed, and the stage is retried
    once by setting hrs_stage = N-1 and Running; a second failure of the same stage leaves the RFP Failed."""
    run = "workflow()?['run']?['name']"
    c = OrderedDict()
    f.add(c, "Failed_Stage", dv_list("hrs_runlogs", "hrs_runlogid,hrs_stage,hrs_startedon",
                                     f"_hrs_rfp_value eq {TRIGGER_RFP} and hrs_correlationid eq '@{{{run}}}' and "
                                     f"hrs_status eq {C('hrs_stagestatus', 'Started')}", None, "createdon desc", 1), after=None)
    known = OrderedDict()
    failed = f"first({value('Failed_Stage')})"
    f.add(known, "Failed_Stage_Row", compose({"id": f"@{failed}?['hrs_runlogid']", "stage": f"@int({failed}?['hrs_stage'])",
                                              "since": f"@{failed}?['hrs_startedon']"}), after=None)
    since = "@{outputs('Failed_Stage_Row')?['since']}"
    delete_created_since(f, known, "Undo", since, TRIGGER_RFP, TRIGGER_KEY)
    f.add(known, "Earlier_Attempt", dv_list("hrs_runlogs", "hrs_status,hrs_errordetail",
                                            f"_hrs_rfp_value eq {TRIGGER_RFP} and hrs_stage eq @{{outputs('Failed_Stage_Row')?['stage']}} and "
                                            f"hrs_runlogid ne @{{outputs('Failed_Stage_Row')?['id']}}", None, "createdon desc", 1))
    earlier = f"first({value('Earlier_Attempt')})"
    retried = (f"and(equals({earlier}?['hrs_status'], {C('hrs_stagestatus', 'Failed')}), "
               f"contains(coalesce({earlier}?['hrs_errordetail'], ''), 'retrying once'))")
    detail = f"concat('Stage ', string(outputs('Failed_Stage_Row')?['stage']), ' failed unexpectedly in flow run ', {run}, '; rows it created were removed; ')"
    retry = OrderedDict()
    f.add(retry, "Log_Retry", dv_update("hrs_runlogs", "@{outputs('Failed_Stage_Row')?['id']}", {
        "hrs_status": C("hrs_stagestatus", "Failed"), "hrs_completedon": "@{utcNow()}",
        "hrs_errordetail": f"@{{concat({detail}, 'retrying once.')}}"}), after=None)
    # Two writes: the pipeline triggers on a status change, so the stage must be in place first (one run at a time:
    # the retry starts when this run ends).
    f.add(retry, "Retry_Rewind", dv_update("hrs_rfps", TRIGGER_RFP, {"hrs_stage": "@sub(outputs('Failed_Stage_Row')?['stage'], 1)",
                                                                     "hrs_status": C("hrs_runstatus", "Not started")}))
    f.add(retry, "Retry_Start", dv_update("hrs_rfps", TRIGGER_RFP, {"hrs_status": RUNNING}))
    stop = OrderedDict()
    f.add(stop, "Log_Stopped", dv_update("hrs_runlogs", "@{outputs('Failed_Stage_Row')?['id']}", {
        "hrs_status": C("hrs_stagestatus", "Failed"), "hrs_completedon": "@{utcNow()}",
        "hrs_errordetail": f"@{{concat({detail}, 'it already failed once, so the run stopped.')}}"}), after=None)
    f.add(stop, "Mark_RFP_Failed", dv_update("hrs_rfps", TRIGGER_RFP, {"hrs_status": FAILED}))
    f.add(known, "If_not_retried", condition({"equals": [f"@{retried}", False]}, retry, stop))
    unknown = OrderedDict()
    # Before any stage started (reading the source, company content): nothing to undo or retry.
    f.add(unknown, "Mark_RFP_Failed_Early", dv_update("hrs_rfps", TRIGGER_RFP, {"hrs_status": FAILED}), after=None)
    f.add(unknown, "Log_Failure", dv_create("hrs_runlogs", {
        "hrs_name": "Pipeline failed", "hrs_rfp@odata.bind": f"hrs_rfps({TRIGGER_RFP})", "hrs_status": C("hrs_stagestatus", "Failed"),
        "hrs_startedon": "@{utcNow()}", "hrs_completedon": "@{utcNow()}", "hrs_correlationid": f"@{{{run}}}",
        "hrs_errordetail": f"@{{concat('The run failed before a stage started; see flow run ', {run}, '.')}}"}))
    f.add(c, "If_stage_known", condition({"greater": [f"@length({value('Failed_Stage')})", 0]}, known, unknown))
    return c


# ==============================================================================================
REMINDER_AFTER = env("hrs_ReminderAfter")
ESCALATE_AFTER = env("hrs_EscalateAfter")


def wait_with_escalation(f: Flow, a: OrderedDict, card_expr: str, update_message: str, reminder_html: str, escalation_html: str) -> list[str]:
    """Post the card and wait (ARCHITECTURE.md 14.4, ADR-0027). No answer within hrs_ReminderAfter: a reminder and
    the card again. No answer within hrs_EscalateAfter: an escalation naming the backup and the card once more,
    waiting hrs_EscalateAfter. Whichever card is answered fills the Response variable; it stays empty when none
    is. Returns the actions the caller's next step must run after (with Succeeded, Skipped and Failed)."""
    recipient = env("hrs_PilotRecipientUpn")
    f.add(a, "Ask", teams_card_and_wait(recipient, card_expr, update_message, REMINDER_AFTER))
    f.add(a, "Got_Ask", set_variable("Response", "@body('Ask')"))
    remind = OrderedDict()
    f.add(remind, "Reminder_Note", teams_message(recipient, reminder_html), after=None)
    f.add(remind, "Remind", teams_card_and_wait(recipient, card_expr, update_message, ESCALATE_AFTER))
    f.add(remind, "Got_Remind", set_variable("Response", "@body('Remind')"))
    escalate = OrderedDict()
    f.add(escalate, "Escalation_Note", teams_message(recipient, escalation_html), after=None)
    f.add(escalate, "Escalate", teams_card_and_wait(recipient, card_expr, update_message, ESCALATE_AFTER))
    f.add(escalate, "Got_Escalate", set_variable("Response", "@body('Escalate')"))
    f.add(remind, "Escalation", scope(escalate), after="Remind")
    remind["Escalation"]["runAfter"] = {"Remind": ["TimedOut", "Failed"]}
    f.add(a, "Reminder", scope(remind), after="Ask")
    a["Reminder"]["runAfter"] = {"Ask": ["TimedOut", "Failed"]}
    return ["Got_Ask", "Reminder"]


def after_wait(a: OrderedDict, name: str, action: dict, waits: list[str]) -> None:
    action["runAfter"] = {w: ["Succeeded", "Skipped", "Failed", "TimedOut"] for w in waits}
    a[name] = action


ANSWERED = "not(empty(variables('Response')))"


def response(*path: str) -> str:
    expr = "variables('Response')"
    for p in path:
        expr += f"?['{p}']"
    return expr


def gap_flow() -> Flow:
    f = Flow("RFP - Route Gap To SME", "Gap card to the pilot recipient (Q6), waits with reminder and escalation, records the answer and proposes a Draft library entry.", agent=False)
    # A gap written as Answered carries an earlier answer and needs no card (ADR-0033).
    f.trigger = dv_trigger("hrs_gap", 1, None, [f"@equals(triggerOutputs()?['body/hrs_smestatus'], {C('hrs_smestatus', 'Routed')})"])
    a = f.actions
    gap_id = "@triggerOutputs()?['body/hrs_gapid']"
    f.add(a, "Init_Response", init_variable("Response", "object", {}), after=None)
    f.add(a, "Gap", dv_get("hrs_gaps", gap_id, None, "hrs_routedtosme($select=hrs_smecode,hrs_name,hrs_email)"))
    f.add(a, "Requirement", dv_get("hrs_requirements", f"@{body('Gap', '_hrs_requirement_value')}", None,
                                   "hrs_rfp($select=hrs_rfpid,hrs_rfpkey,hrs_duedate),hrs_categorycode($select=hrs_categoryid,hrs_categorycode,hrs_name)"))
    f.add(a, "Backup", dv_get("hrs_categories", f"@{{{body('Requirement', 'hrs_categorycode')}?['hrs_categoryid']}}", "hrs_categorycode",
                              "hrs_backupsmeid($select=hrs_name)"))
    card = {
        "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.4",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": "No approved library answer - your input is needed"},
            {"type": "FactSet", "facts": [
                {"title": "Requirement", "value": f"@{{{body('Requirement', 'hrs_reqid')}}}"},
                {"title": "Location", "value": f"@{{{body('Requirement', 'hrs_location')}}}"},
                {"title": "Category", "value": f"@{{{body('Requirement', 'hrs_categorycode')}?['hrs_name']}}"},
                {"title": "Routed to", "value": f"@{{{body('Gap', 'hrs_routedtosme')}?['hrs_name']}} (pilot delivery)"},
                {"title": "Answer by", "value": f"@{{{body('Gap', 'hrs_dueby')}}}"}]},
            {"type": "TextBlock", "wrap": True, "text": f"@{{{body('Requirement', 'hrs_requirementtext')}}}"},
            {"type": "Input.Text", "id": "answer", "isMultiline": True, "placeholder": "Your answer for this proposal"}],
        "actions": [{"type": "Action.Submit", "title": "Submit answer", "data": {"decision": "answer"}},
                    {"type": "Action.Submit", "title": "Cannot answer", "data": {"decision": "cannot"}}]}
    f.add(a, "Card", compose(card))
    req = body("Requirement", "hrs_reqid")
    sme = f"{body('Gap', 'hrs_routedtosme')}?['hrs_name']"
    waits = wait_with_escalation(
        f, a, "@{string(outputs('Card'))}", "Thanks - recorded.",
        f"@{{concat('<p>Reminder: requirement ', {req}, ' still needs an answer from ', {sme}, '.</p>')}}",
        f"@{{concat('<p>Escalated: no answer from ', {sme}, ' for requirement ', {req}, '. Backup SME: ', "
        f"coalesce({body('Backup', 'hrs_backupsmeid')}?['hrs_name'], 'none recorded'), ' (pilot delivery).</p>')}}")
    # A gap undone by a rerun or a failed stage (ADR-0026) no longer exists; say so rather than record into nothing (Q9).
    after_wait(a, "Gap_Now", dv_list("hrs_gaps", "hrs_gapid", "hrs_gapid eq @{triggerOutputs()?['body/hrs_gapid']}"), waits)
    withdrawn = OrderedDict()
    f.add(withdrawn, "Tell_Withdrawn", teams_message(env("hrs_PilotRecipientUpn"), f"@{{concat('<p>The card for requirement ', {req}, "
                                                     f"' was withdrawn because the run was reprocessed; nothing was recorded.</p>')}}"), after=None)
    f.add(withdrawn, "Withdrawn_Stop", terminate("Succeeded"))
    f.add(a, "If_withdrawn", condition({"equals": [f"@length({value('Gap_Now')})", 0]}, withdrawn, OrderedDict()))
    answered = OrderedDict()
    f.add(answered, "Record_Answer", dv_update("hrs_gaps", gap_id, {
        "hrs_smeanswertext": f"@{{{response('data', 'answer')}}}",
        "hrs_smestatus": C("hrs_smestatus", "Answered")}), after=None)
    # An addendum rerun routes the same requirement again, and hrs_answercode is unique (ADR-0032). A new answer replaces an
    # earlier Draft proposal. A proposal that has since left Draft belongs to the library's reviewers and isn't changed.
    code = f"concat('PROP-', {body('Requirement', 'hrs_rfp')}?['hrs_rfpkey'], '-', {req})"
    f.add(answered, "Existing_Proposal", dv_list("hrs_answers", "hrs_answerid,hrs_approvalstatus", f"hrs_answercode eq '@{{{code}}}'"))
    existing = f"first({value('Existing_Proposal')})"
    new_proposal = OrderedDict()
    f.add(new_proposal, "Propose_Library_Entry", dv_create("hrs_answers", {
        "hrs_answercode": f"@{{{code}}}",
        "hrs_question": f"@{body('Requirement', 'hrs_requirementtext')}", "hrs_answertext": f"@{{{response('data', 'answer')}}}",
        "hrs_approvalstatus": C("hrs_approvalstatus", "Draft"), "hrs_confidentiality": C("hrs_confidentiality", "Public"),
        "hrs_answertype": C("hrs_answertype", "Short"),
        "hrs_categorycode@odata.bind": f"hrs_categories(@{{{body('Requirement', 'hrs_categorycode')}?['hrs_categoryid']}})",
        "hrs_owneremail": f"@{{{body('Gap', 'hrs_routedtosme')}?['hrs_email']}}"}), after=None)
    f.add(new_proposal, "Link_Proposal", dv_update("hrs_gaps", gap_id, {
        "hrs_proposedanswer@odata.bind": f"hrs_answers(@{{{body('Propose_Library_Entry', 'hrs_answerid')}}})"}))
    known_proposal = OrderedDict()
    replace = OrderedDict()
    f.add(replace, "Replace_Draft_Proposal", dv_update("hrs_answers", f"@{{{existing}?['hrs_answerid']}}", {
        "hrs_question": f"@{body('Requirement', 'hrs_requirementtext')}", "hrs_answertext": f"@{{{response('data', 'answer')}}}"}), after=None)
    f.add(known_proposal, "If_still_draft", condition({"equals": [f"@{existing}?['hrs_approvalstatus']", C("hrs_approvalstatus", "Draft")]},
                                                      replace, OrderedDict()), after=None)
    f.add(known_proposal, "Link_Existing_Proposal", dv_update("hrs_gaps", gap_id, {
        "hrs_proposedanswer@odata.bind": f"hrs_answers(@{{{existing}?['hrs_answerid']}})"}))
    f.add(answered, "If_new_proposal", condition({"equals": [f"@length({value('Existing_Proposal')})", 0]}, new_proposal, known_proposal))
    cannot = OrderedDict()
    f.add(cannot, "Record_Cannot", dv_update("hrs_gaps", gap_id, {"hrs_smestatus": C("hrs_smestatus", "Cannot answer")}), after=None)
    respond = OrderedDict()
    f.add(respond, "If_answered", condition({"and": [{"equals": [f"@{response('data', 'decision')}", "answer"]},
                                                      {"not": {"equals": [f"@trim(coalesce({response('data', 'answer')}, ''))", ""]}}]},
                                             answered, cannot), after=None)
    overdue = OrderedDict()
    # No answer after the reminder and the escalation: the gap stays a gap and is surfaced on the approval card (14.4).
    f.add(overdue, "Record_Overdue", dv_update("hrs_gaps", gap_id, {"hrs_smestatus": C("hrs_smestatus", "Overdue")}), after=None)
    f.add(a, "If_responded", condition({"equals": [f"@{ANSWERED}", True]}, respond, overdue))
    f.add(a, "Still_routed", dv_list("hrs_gaps", "hrs_gapid",
                                     f"hrs_requirement/_hrs_rfp_value eq @{{{body('Requirement', 'hrs_rfp', 'hrs_rfpid')}}} and hrs_smestatus eq {C('hrs_smestatus', 'Routed')}"))
    # A failed library write must not strand the run: the answer is already saved, and a gap whose answer failed to save stays Routed.
    a["Still_routed"]["runAfter"] = {"If_responded": ["Succeeded", "Failed"]}
    resume = OrderedDict()
    f.add(resume, "Resume_Pipeline", dv_update("hrs_rfps", f"@{{{body('Requirement', 'hrs_rfp', 'hrs_rfpid')}}}", {"hrs_status": RUNNING}), after=None)
    f.add(a, "If_all_answered", condition({"equals": [f"@length({value('Still_routed')})", 0]}, resume, OrderedDict()))
    return f


# ==============================================================================================
def verify_flow() -> Flow:
    """RFP - Verify OCR Requirement (ADR-0025): one card per OCR-LOW-CONFIDENCE flag, with the page-image crop beside the
    text read by OCR. Confirm keeps the text; Correct replaces it (the OCR reading stays in Original Text). When the last
    flag of the RFP is resolved, the pipeline resumes from stage 3. No answer after the reminder and escalation leaves
    the flag open and the run waiting: OCR text is never accepted by default (ADR-0027)."""
    f = Flow("RFP - Verify OCR Requirement", "OCR verification card with a page-image crop; records the verified text and resumes the pipeline.",
             agent=False)
    ocr = C("hrs_flagtype", "OCR-LOW-CONFIDENCE")
    f.trigger = dv_trigger("hrs_flag", 1, None, [f"@equals(triggerOutputs()?['body/hrs_flagtype'], {ocr})"])
    a = f.actions
    flag_id = "@triggerOutputs()?['body/hrs_flagid']"
    f.add(a, "Init_Response", init_variable("Response", "object", {}), after=None)
    f.add(a, "Flag", dv_get("hrs_flags", flag_id))
    f.add(a, "Requirement", dv_get("hrs_requirements", f"@{body('Flag', '_hrs_requirement_value')}", None,
                                   "hrs_rfp($select=hrs_rfpid,hrs_rfpkey),hrs_sourceblock($select=hrs_polygon,hrs_pagenumber)"))
    f.add(a, "Base_Revision", dv_list("hrs_rfprevisions", "hrs_sourcedocument",
                                      f"_hrs_rfp_value eq @{{{body('Requirement', 'hrs_rfp')}?['hrs_rfpid']}} and hrs_revisionnumber eq 0"))
    f.add(a, "Source", sp_get_content_by_path(f"@{{first({value('Base_Revision')})?['hrs_sourcedocument']}}"))
    f.add(a, "Crop", tools("OcrCrop", {"contentBase64": f"@{body('Source', '$content')}",
                                        "regions": f"@{{{body('Requirement', 'hrs_sourceblock')}?['hrs_polygon']}}"}))
    card = {
        "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.4",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": "Please verify text read from a scanned page"},
            {"type": "FactSet", "facts": [
                {"title": "Requirement", "value": f"@{{{body('Requirement', 'hrs_reqid')}}}"},
                {"title": "Location", "value": f"@{{{body('Requirement', 'hrs_location')}}}"},
                {"title": "Why", "value": f"@{{{body('Flag', 'hrs_detail')}}}"}]},
            {"type": "TextBlock", "weight": "Bolder", "text": "The page"},
            {"type": "Image", "url": f"@{{concat('data:image/jpeg;base64,', {body('Crop', 'jpegBase64')})}}", "altText": "Scanned page crop"},
            {"type": "TextBlock", "weight": "Bolder", "text": "Text read by OCR (correct it if it differs from the page)"},
            {"type": "Input.Text", "id": "text", "isMultiline": True, "value": f"@{{{body('Requirement', 'hrs_requirementtext')}}}"}],
        "actions": [{"type": "Action.Submit", "title": "Text is correct", "data": {"decision": "confirm"}},
                    {"type": "Action.Submit", "title": "Save my correction", "data": {"decision": "correct"}}]}
    f.add(a, "Card", compose(card))
    req = body("Requirement", "hrs_reqid")
    waits = wait_with_escalation(
        f, a, "@{string(outputs('Card'))}", "Thanks - recorded.",
        f"@{{concat('<p>Reminder: the OCR text of requirement ', {req}, ' is waiting for verification; the run is paused until it is checked.</p>')}}",
        f"@{{concat('<p>Escalated: the OCR text of requirement ', {req}, ' has not been verified. The run stays paused; it never accepts OCR text by default.</p>')}}")
    rfp_id = f"{body('Requirement', 'hrs_rfp')}?['hrs_rfpid']"
    after_wait(a, "Flag_Now", dv_list("hrs_flags", "hrs_flagid", "hrs_flagid eq @{triggerOutputs()?['body/hrs_flagid']}"), waits)
    withdrawn = OrderedDict()
    f.add(withdrawn, "Tell_Withdrawn", teams_message(env("hrs_PilotRecipientUpn"), f"@{{concat('<p>The verification card for requirement ', {req}, "
                                                     f"' was withdrawn because the run was reprocessed; nothing was recorded.</p>')}}"), after=None)
    f.add(withdrawn, "Withdrawn_Stop", terminate("Succeeded"))
    f.add(a, "If_withdrawn", condition({"equals": [f"@length({value('Flag_Now')})", 0]}, withdrawn, OrderedDict()))
    overdue = OrderedDict()
    f.add(overdue, "Log_Overdue", dv_create("hrs_runlogs", {
        "hrs_name": f"@{{concat('Verification overdue ', {req})}}", "hrs_rfp@odata.bind": f"hrs_rfps(@{{{rfp_id}}})", "hrs_stage": 2,
        "hrs_status": C("hrs_stagestatus", "Failed"), "hrs_startedon": "@{utcNow()}", "hrs_completedon": "@{utcNow()}",
        "hrs_errordetail": f"@{{concat('No verification of ', {req}, ' after the reminder and escalation; the flag stays open and the run waits.')}}"}),
          after=None)
    f.add(overdue, "Overdue_Stop", terminate("Succeeded"))
    f.add(a, "If_unanswered", condition({"equals": [f"@{ANSWERED}", False]}, overdue, OrderedDict()))
    corrected = OrderedDict()
    f.add(corrected, "Save_Correction", dv_update("hrs_requirements", f"@{body('Flag', '_hrs_requirement_value')}",
                                                  {"hrs_requirementtext": f"@{{trim({response('data', 'text')})}}"}), after=None)
    f.add(a, "If_corrected", condition({"and": [
        {"equals": [f"@{response('data', 'decision')}", "correct"]},
        {"not": {"equals": [f"@trim(coalesce({response('data', 'text')}, ''))", ""]}},
        {"not": {"equals": [f"@trim(coalesce({response('data', 'text')}, ''))", f"@{body('Requirement', 'hrs_requirementtext')}"]}}]},
        corrected, OrderedDict()))
    f.add(a, "Resolve_Flag", dv_update("hrs_flags", flag_id, {
        "hrs_resolvedon": "@{utcNow()}", "hrs_resolvedby": env("hrs_PilotRecipientUpn"),
        "hrs_detail": f"@{{concat({body('Flag', 'hrs_detail')}, ' Verified: ', {response('data', 'decision')}, '.')}}"}))
    f.add(a, "Still_open", dv_list("hrs_flags", "hrs_flagid",
                                   f"_hrs_rfp_value eq @{{{rfp_id}}} and hrs_flagtype eq {ocr} and hrs_resolvedon eq null"))
    f.add(a, "RFP_Now", dv_get("hrs_rfps", f"@{{{rfp_id}}}"))
    resume = OrderedDict()
    f.add(resume, "Resume_Pipeline", dv_update("hrs_rfps", f"@{{{rfp_id}}}", {"hrs_status": RUNNING}), after=None)
    f.add(a, "If_all_verified", condition({"and": [{"equals": [f"@length({value('Still_open')})", 0]},
                                                   {"equals": [f"@{body('RFP_Now', 'hrs_status')}", AWAITING]},
                                                   {"equals": [f"@{body('RFP_Now', 'hrs_stage')}", 2]}]}, resume, OrderedDict()))
    return f


def approval_flow() -> Flow:
    f = Flow("RFP - Request Approval", "Single approval (9.3): card to the pilot recipient, waits with reminder and escalation; approve files the package, reject returns to assembly.", agent=False)
    f.trigger = dv_trigger("hrs_approval", 1)
    a = f.actions
    approval_id = "@triggerOutputs()?['body/hrs_approvalid']"
    rfp_id = "@triggerOutputs()?['body/_hrs_rfp_value']"
    name = "triggerOutputs()?['body/hrs_name']"
    f.add(a, "Init_Response", init_variable("Response", "object", {}), after=None)
    f.add(a, "Payload", compose("@json(triggerOutputs()?['body/hrs_cardpayload'])"))
    # The approver table has no backup column; escalation names the next approval level up (14.4).
    f.add(a, "Approvers", dv_list("hrs_approvers", "hrs_level,hrs_approvername", None, None, "hrs_level"))
    f.add(a, "Next_Level", {"type": "Query", "inputs": {"from": f"@{value('Approvers')}",
                                                         "where": "@greater(item()?['hrs_level'], triggerOutputs()?['body/hrs_level'])"}})
    waits = wait_with_escalation(
        f, a, "@{string(outputs('Payload')?['card'])}", "Decision recorded.",
        f"@{{concat('<p>Reminder: ', {name}, ' is waiting for your decision.</p>')}}",
        f"@{{concat('<p>Escalated: no decision on ', {name}, '. Next approval level: ', "
        f"coalesce(first(body('Next_Level'))?['hrs_approvername'], 'none - this is the top level'), ' (pilot delivery).</p>')}}")
    after_wait(a, "Approval_Now", dv_list("hrs_approvals", "hrs_approvalid", "hrs_approvalid eq @{triggerOutputs()?['body/hrs_approvalid']}"), waits)
    withdrawn = OrderedDict()
    f.add(withdrawn, "Tell_Withdrawn", teams_message(env("hrs_PilotRecipientUpn"), f"@{{concat('<p>', {name}, "
                                                     f"' was withdrawn because the run was reprocessed; the decision was not recorded.</p>')}}"), after=None)
    f.add(withdrawn, "Withdrawn_Stop", terminate("Succeeded"))
    f.add(a, "If_withdrawn", condition({"equals": [f"@length({value('Approval_Now')})", 0]}, withdrawn, OrderedDict()))
    overdue = OrderedDict()
    # No decision after the reminder and escalation: the RFP stays Awaiting human and the run log says why.
    f.add(overdue, "Log_Overdue", dv_create("hrs_runlogs", {
        "hrs_name": "Approval overdue", "hrs_rfp@odata.bind": "hrs_rfps(@{triggerOutputs()?['body/_hrs_rfp_value']})", "hrs_stage": 9,
        "hrs_status": C("hrs_stagestatus", "Failed"), "hrs_startedon": "@{utcNow()}", "hrs_completedon": "@{utcNow()}",
        "hrs_errordetail": f"@{{concat('No decision on ', {name}, ' after the reminder and escalation; the RFP stays Awaiting human.')}}"}), after=None)
    f.add(overdue, "Overdue_Stop", terminate("Succeeded"))
    f.add(a, "If_unanswered", condition({"equals": [f"@{ANSWERED}", False]}, overdue, OrderedDict()))
    f.add(a, "Decision", compose({"approve": f"@equals({response('data')}?['decision'], 'approve')",
                                  "comments": f"@{{coalesce({response('data')}?['comments'], '')}}"}))
    f.add(a, "Record_Decision", dv_update("hrs_approvals", approval_id, {
        "hrs_decision": f"@if(outputs('Decision')?['approve'], {C('hrs_approvaldecision', 'Approved')}, {C('hrs_approvaldecision', 'Rejected')})",
        "hrs_comments": "@{outputs('Decision')?['comments']}", "hrs_decidedon": "@{utcNow()}"}))
    approve = OrderedDict()
    copy_loop = OrderedDict()
    f.add(copy_loop, "Draft_File", sp_get_content_by_path("@{items('File_Package')}"), after=None)
    f.add(copy_loop, "Filed_File", sp_create_file(f"@{{concat({env_inline('hrs_OutputLibraryUrl')}, '/Filed')}}",
                                                  "@{last(split(items('File_Package'), '/'))}", "@body('Draft_File')"))
    f.add(approve, "File_Package", foreach("@createArray(outputs('Payload')?['files']?['Narrative'], outputs('Payload')?['files']?['Excluded'], outputs('Payload')?['files']?['Compliance-Matrix'])",
                                           copy_loop, 3), after=None)
    f.add(approve, "Complete_RFP", dv_update("hrs_rfps", rfp_id, {"hrs_status": COMPLETED, "hrs_stage": 9}))
    reject = OrderedDict()
    f.add(reject, "Back_To_Assembly", dv_update("hrs_rfps", rfp_id, {"hrs_stage": 7, "hrs_status": RUNNING}), after=None)
    f.add(a, "If_approved", condition({"equals": ["@outputs('Decision')?['approve']", True]}, approve, reject))
    return f


# ==============================================================================================
# RFP Orchestrator tools (ADR-0030): agent flows the agent calls. Each answers within the 100-second tool limit
# (https://learn.microsoft.com/en-us/microsoft-copilot-studio/flow-agent); the text comes from rfp-tools.
NO_ID = "00000000-0000-0000-0000-000000000000"


def agent_tool(name: str, description: str, inputs: dict[str, str]) -> Flow:
    f = Flow(name, description, agent=True)
    f.trigger = {"manual": {"type": "Request", "kind": "Skills", "inputs": {"schema": {
        "type": "object", "required": list(inputs),
        "properties": {k: {"title": k, "description": d, "type": "string", "x-ms-content-hint": "TEXT", "x-ms-dynamically-added": True}
                       for k, d in inputs.items()}}}}}
    return f


def respond(text_expr: str) -> dict:
    return {"type": "Response", "kind": "Skills", "inputs": {"statusCode": 200, "body": {"result": text_expr}, "schema": {
        "type": "object", "properties": {"result": {"title": "result", "description": "The answer for the user", "type": "string",
                                                    "x-ms-content-hint": "TEXT", "x-ms-dynamically-added": True}}}}}


KEY_INPUT = "The RFP's key, as the user gave it. The tool says if no RFP has that key"
REQ_INPUT = "The requirement ID as the buyer wrote it, for example CV-047 or THPA-036"


def tool_rfp(f: Flow, a: OrderedDict) -> str:
    f.add(a, "RFP", dv_list("hrs_rfps", "hrs_rfpid,hrs_rfpkey,hrs_stage,hrs_status,hrs_currentrevision",
                            "hrs_rfpkey eq '@{trim(triggerBody()?['rfpKey'])}'"), after=None)
    return f"@{{coalesce(first({value('RFP')})?['hrs_rfpid'], '{NO_ID}')}}"


def tool_audit(f: Flow, a: OrderedDict, tool: str, outcome: str = "'ran'") -> None:
    """The tool writes its own hrs_runlog row, so the record doesn't depend on a hook, which fails silently (ADR-0034).
    Who and where come from the request headers the platform sets, not from anything the model fills in.
    [UNVERIFIED] Learn doesn't document these headers; a missing one is recorded as 'unknown'. In Teams x-ms-object-id is
    the Teams user ID (29:...), not the Entra object ID."""
    header = lambda name: f"coalesce(triggerOutputs()?['headers']?['{name}'], 'unknown')"
    row = {"hrs_name": f"Tool run: {tool}", "hrs_status": C("hrs_stagestatus", "Succeeded"),
           "hrs_startedon": "@{utcNow()}", "hrs_completedon": "@{utcNow()}",
           "hrs_correlationid": f"@{{take({header('x-ms-client-session-id')}, 100)}}",
           "hrs_errordetail": f"@{{concat('Audit (RFP Orchestrator). Tool: {tool}; parameters: ', string(triggerBody()), '; outcome: ', {outcome}, "
                              f"'; caller: ', {header('x-ms-object-id')}, '; client: ', {header('x-ms-client-keywords')}, "
                              f"'; conversation: ', {header('x-ms-client-session-id')}, '.')}}"}
    with_rfp = OrderedDict()
    f.add(with_rfp, "Audit_Tool_RFP", dv_create("hrs_runlogs", {**row, "hrs_rfp@odata.bind": f"hrs_rfps(@{{first({value('RFP')})?['hrs_rfpid']}})"}), after=None)
    without = OrderedDict()
    f.add(without, "Audit_Tool", dv_create("hrs_runlogs", row), after=None)
    f.add(a, "If_audit", condition({"greater": [f"@length({value('RFP')})", 0]}, with_rfp, without))


def after_audit(a: OrderedDict, name: str) -> None:
    """A read tool still answers if its audit row couldn't be written."""
    a[name]["runAfter"] = {"If_audit": ["Succeeded", "Failed"]}


def status_tool_flow() -> Flow:
    f = agent_tool("RFP - Get Run Status", "Where an RFP is in the pipeline, what it is waiting for, and its recent run log.",
                   {"rfpKey": KEY_INPUT})
    a = f.actions
    rid = tool_rfp(f, a)
    tool_audit(f, a, "RFP - Get Run Status")
    # Audit rows are left out, or asking for status would push the stage rows out of the 8.
    f.add(a, "Run_Log", dv_list("hrs_runlogs", "hrs_name,hrs_status,hrs_creditsestimate,hrs_errordetail",
                                f"_hrs_rfp_value eq {rid} and not startswith(hrs_name,'Tool ')", None, "createdon desc", 8))
    after_audit(a, "Run_Log")
    f.add(a, "Gaps", dv_list("hrs_gaps", "hrs_smestatus", f"hrs_requirement/_hrs_rfp_value eq {rid}"))
    f.add(a, "Ocr_Open", dv_list("hrs_flags", "hrs_flagid", f"_hrs_rfp_value eq {rid} and hrs_flagtype eq "
                                 f"{C('hrs_flagtype', 'OCR-LOW-CONFIDENCE')} and hrs_resolvedon eq null"))
    f.add(a, "Approvals", dv_list("hrs_approvals", "hrs_level,hrs_decision", f"_hrs_rfp_value eq {rid}", None, "createdon desc"))
    f.add(a, "Status", tools("OrchestratorRunStatus", {"rfp": f"@first({value('RFP')})", "runlogs": f"@{value('Run_Log')}",
                                                        "gaps": f"@{value('Gaps')}", "ocrOpen": f"@{value('Ocr_Open')}",
                                                        "approvals": f"@{value('Approvals')}"}))
    f.add(a, "Respond", respond(f"@{body('Status', 'text')}"))
    return f


def tool_requirement(f: Flow, a: OrderedDict, rid: str) -> str:
    f.add(a, "Requirement", dv_list("hrs_requirements", "hrs_requirementid,hrs_reqid,hrs_location,hrs_requirementtext,hrs_compliance",
                                    f"_hrs_rfp_value eq {rid} and hrs_reqid eq '@{{trim(triggerBody()?['reqId'])}}'"))
    return f"@{{coalesce(first({value('Requirement')})?['hrs_requirementid'], '{NO_ID}')}}"


def flag_tool_flow() -> Flow:
    f = agent_tool("RFP - Explain Flag", "Every flag on one requirement: type, severity, the policy rule and its approved position, and the reason.",
                   {"rfpKey": KEY_INPUT, "reqId": REQ_INPUT})
    a = f.actions
    rid = tool_rfp(f, a)
    tool_audit(f, a, "RFP - Explain Flag")
    req = tool_requirement(f, a, rid)
    after_audit(a, "Requirement")
    f.add(a, "Flags", dv_list("hrs_flags", "hrs_flagtype,hrs_severity,hrs_detail,hrs_resolvedby", f"_hrs_requirement_value eq {req}",
                              "hrs_policyrule($select=hrs_ruleid,hrs_approvedposition)"))
    f.add(a, "Explain", tools("OrchestratorExplainFlags", {"requirement": f"@first({value('Requirement')})", "flags": f"@{value('Flags')}"}))
    f.add(a, "Respond", respond(f"@{body('Explain', 'text')}"))
    return f


def answer_tool_flow() -> Flow:
    f = agent_tool("RFP - Explain Answer", "Why a requirement was answered as it was: compliance value, the approved library answer ID, "
                   "the response text, or the gap if no approved answer existed.", {"rfpKey": KEY_INPUT, "reqId": REQ_INPUT})
    a = f.actions
    rid = tool_rfp(f, a)
    tool_audit(f, a, "RFP - Explain Answer")
    req = tool_requirement(f, a, rid)
    after_audit(a, "Requirement")
    f.add(a, "Responses", dv_list("hrs_responses", "hrs_responsetext,hrs_responsecode", f"_hrs_requirement_value eq {req}",
                                  "hrs_answer($select=hrs_answercode,hrs_question,hrs_lastreviewed)"))
    f.add(a, "Gaps", dv_list("hrs_gaps", "hrs_smestatus,hrs_smeanswertext", f"_hrs_requirement_value eq {req}", "hrs_routedtosme($select=hrs_name)"))
    f.add(a, "Explain", tools("OrchestratorExplainAnswer", {"requirement": f"@first({value('Requirement')})",
                                                             "responses": f"@{value('Responses')}", "gaps": f"@{value('Gaps')}"}))
    f.add(a, "Respond", respond(f"@{body('Explain', 'text')}"))
    return f


def rerun_tool_flow() -> Flow:
    """Reruns from stage N with ADR-0026's rule: rows created since stage N's latest pass are removed, then the pipeline
    resumes at N. rfp-tools decides whether it's allowed; the Pre tool use hook repeats that check but can fail open."""
    f = agent_tool("RFP - Rerun Stage", "Rerun an RFP's pipeline from a stage (1-9). Refused while the pipeline is running or after the RFP is filed.",
                   {"rfpKey": KEY_INPUT, "stage": "The stage to rerun from, 1 to 9"})
    a = f.actions
    rid = tool_rfp(f, a)
    f.add(a, "Check", tools("OrchestratorRerunCheck", {"rfp": f"@first({value('RFP')})", "stage": "@{triggerBody()?['stage']}"}))
    # Written before anything changes. If_allowed runs only if this succeeded, so a rerun that can't be audited doesn't happen.
    tool_audit(f, a, "RFP - Rerun Stage", f"if(equals({body('Check', 'allowed')}, true), 'allowed', concat('refused: ', {body('Check', 'reason')}))")
    # Say why, or the agent reports a service error and suggests retrying, which would fail the same way.
    f.add(a, "Respond_Not_Audited", respond("Not rerun, and nothing was changed: the audit record could not be written. Trying again "
                                            "won't help until that is fixed. Ask an administrator to check the latest RFP - Rerun Stage flow run."),
          after=None)
    a["Respond_Not_Audited"]["runAfter"] = {"If_audit": ["Failed", "TimedOut"]}
    refuse = OrderedDict()
    f.add(refuse, "Respond_Refused", respond(f"@{{concat('Not rerun: ', {body('Check', 'reason')})}}"), after=None)
    go = OrderedDict()
    # Respond first: removing rows can take longer than the tool limit; the run log records the outcome.
    f.add(go, "Respond_Started", respond(f"@{{concat({body('Check', 'reason')}, ' The pipeline restarts once rows from the earlier pass are removed; "
                                         f"ask for the run status to follow it.')}}"), after=None)
    f.add(go, "Pass", dv_list("hrs_runlogs", "hrs_startedon", f"_hrs_rfp_value eq {rid} and hrs_stage eq @{{{body('Check', 'fromStage')}}}",
                              None, "createdon desc", 1))
    delete_created_since(f, go, "Rerun", f"@{{coalesce(first({value('Pass')})?['hrs_startedon'], utcNow())}}", rid,
                         f"@{{first({value('RFP')})?['hrs_rfpkey']}}")
    f.add(go, "Rewind", dv_update("hrs_rfps", rid, {
        "hrs_stage": f"@sub({body('Check', 'fromStage')}, 1)", "hrs_status": C("hrs_runstatus", "Not started"),
        "hrs_currentrevision": f"@if({body('Check', 'resetRevision')}, 0, first({value('RFP')})?['hrs_currentrevision'])"}))
    f.add(go, "Restart", dv_update("hrs_rfps", rid, {"hrs_status": RUNNING}))
    f.add(go, "Log_Rerun", dv_create("hrs_runlogs", {
        "hrs_name": f"@{{concat('Rerun from stage ', string({body('Check', 'fromStage')}), ' (RFP Orchestrator)')}}",
        "hrs_rfp@odata.bind": f"hrs_rfps({rid})", "hrs_status": C("hrs_stagestatus", "Succeeded"),
        "hrs_startedon": "@{utcNow()}", "hrs_completedon": "@{utcNow()}", "hrs_correlationid": "@{workflow()?['run']?['name']}",
        "hrs_errordetail": f"@{body('Check', 'reason')}"}))
    f.add(a, "If_allowed", condition({"equals": [f"@{body('Check', 'allowed')}", True]}, go, refuse), after="If_audit")
    return f


def hook_flow(name: str, description: str, extra_inputs: dict[str, str], outputs: dict[str, str]) -> Flow:
    """A hook's workflow (ADR-0030): the inputs every hook event sends (event, timestamp, metadata) plus the event's own,
    and the event's outputs, with the field names Copilot Studio matches on
    (https://learn.microsoft.com/en-us/microsoft-copilot-studio/agents-experience/hooks-overview)."""
    f = Flow(name, description, agent=True)
    inputs = {"event": "string", "timestamp": "string", "metadata": "object", **extra_inputs}
    f.trigger = {"manual": {"type": "Request", "kind": "Skills", "inputs": {"schema": {
        "type": "object", "required": list(inputs),
        "properties": {k: {"title": k, "type": t, **({"x-ms-content-hint": "TEXT"} if t == "string" else {}), "x-ms-dynamically-added": True}
                       for k, t in inputs.items()}}}}}
    f.hook_outputs = outputs
    return f


def hook_respond(f: Flow, body: dict) -> dict:
    hints = {"string": "TEXT", "boolean": "BOOLEAN"}
    return {"type": "Response", "kind": "Skills", "inputs": {"statusCode": 200, "body": body, "schema": {
        "type": "object", "properties": {k: {"title": k, "type": t, **({"x-ms-content-hint": hints[t]} if t in hints else {}),
                                             "x-ms-dynamically-added": True} for k, t in f.hook_outputs.items()}}}}


def pre_tool_hook_flow() -> Flow:
    """Pre tool use, scoped to RFP - Rerun Stage: deny a rerun the tool itself would refuse (running, filed, stage out of
    range). Defense in depth only (ARCHITECTURE.md 10.2): hooks fail open, and the tool runs the same check."""
    f = hook_flow("RFP - Hook Pre Tool Use", "Hook: deny an RFP rerun while the pipeline is running, after filing, or for a stage outside 1-9.",
                  {"toolName": "string", "parameters": "object"},
                  {"permissionDecision": "string", "permissionDecisionReason": "string", "modifiedParameters": "object",
                   "additionalContext": "string", "suppressOutput": "boolean"})
    a = f.actions
    f.add(a, "RFP", dv_list("hrs_rfps", "hrs_rfpid,hrs_rfpkey,hrs_stage,hrs_status",
                            "hrs_rfpkey eq '@{trim(string(coalesce(triggerBody()?['parameters']?['rfpKey'], '')))}'"), after=None)
    f.add(a, "Check", tools("OrchestratorRerunCheck", {"rfp": f"@first({value('RFP')})",
                                                        "stage": "@{triggerBody()?['parameters']?['stage']}"}))
    rerun = "contains(toLower(coalesce(triggerBody()?['toolName'], '')), 'rerun')"
    denied = f"and({rerun}, not(equals({body('Check', 'allowed')}, true)))"
    # The Post tool use hook never fires for a denied call, so the denial writes its own audit row (ADR-0032).
    meta = "triggerBody()?['metadata']"
    audit = {"hrs_name": "@{concat('Tool call denied: ', triggerBody()?['toolName'])}", "hrs_status": C("hrs_stagestatus", "Succeeded"),
             "hrs_startedon": "@{utcNow()}", "hrs_completedon": "@{utcNow()}", "hrs_correlationid": conversation_key(meta),
             "hrs_errordetail": f"@{{concat('Audit (RFP Orchestrator). Denied tool: ', triggerBody()?['toolName'], '; parameters: ', string(triggerBody()?['parameters']), "
                                f"'; reason: ', coalesce({body('Check', 'reason')}, ''), '; user: ', coalesce({meta}?['userAadObjectId'], 'not signed in'), "
                                f"'; channel: ', coalesce({meta}?['channel'], ''), '; conversation: ', coalesce({meta}?['conversationId'], ''), '.')}}"}
    with_rfp = OrderedDict()
    f.add(with_rfp, "Audit_Denied_RFP", dv_create("hrs_runlogs", {**audit, "hrs_rfp@odata.bind": f"hrs_rfps(@{{first({value('RFP')})?['hrs_rfpid']}})"}), after=None)
    without = OrderedDict()
    f.add(without, "Audit_Denied", dv_create("hrs_runlogs", audit), after=None)
    log_denial = OrderedDict()
    f.add(log_denial, "If_rfp", condition({"greater": [f"@length({value('RFP')})", 0]}, with_rfp, without), after=None)
    f.add(a, "If_denied", condition({"equals": [f"@{denied}", True]}, log_denial, OrderedDict()))
    f.add(a, "Respond", hook_respond(f, {
        "permissionDecision": f"@{{if({denied}, 'deny', '')}}",
        "permissionDecisionReason": f"@{{if({denied}, {body('Check', 'reason')}, '')}}"}))
    # A failed audit write must not turn a denial into an allowed call.
    a["Respond"]["runAfter"] = {"If_denied": ["Succeeded", "Failed"]}
    return f


def post_tool_hook_flow() -> Flow:
    """Post tool use, all tools: one audit row per call in hrs_runlog (tool, parameters, user, conversation). The tools
    return run data and requirement text only, never buyer contact details, so there is nothing to redact (10.1)."""
    f = hook_flow("RFP - Hook Post Tool Use", "Hook: write an audit row for every RFP Orchestrator tool call.",
                  {"toolName": "string", "parameters": "object", "result": "object"},
                  {"modifiedResult": "string", "additionalContext": "string", "suppressOutput": "boolean"})
    a = f.actions
    meta = "triggerBody()?['metadata']"
    f.add(a, "RFP", dv_list("hrs_rfps", "hrs_rfpid", "hrs_rfpkey eq '@{trim(string(coalesce(triggerBody()?['parameters']?['rfpKey'], '')))}'"),
          after=None)
    audit = {"hrs_name": "@{concat('Tool call: ', triggerBody()?['toolName'])}", "hrs_status": C("hrs_stagestatus", "Succeeded"),
             "hrs_startedon": "@{utcNow()}", "hrs_completedon": "@{utcNow()}", "hrs_correlationid": conversation_key(meta),
             "hrs_errordetail": f"@{{concat('Audit (RFP Orchestrator). Tool: ', triggerBody()?['toolName'], '; parameters: ', string(triggerBody()?['parameters']), "
                                f"'; user: ', coalesce({meta}?['userAadObjectId'], 'not signed in'), '; channel: ', coalesce({meta}?['channel'], ''), "
                                f"'; conversation: ', coalesce({meta}?['conversationId'], ''), '.')}}"}
    with_rfp = OrderedDict()
    f.add(with_rfp, "Audit_RFP", dv_create("hrs_runlogs", {**audit, "hrs_rfp@odata.bind": f"hrs_rfps(@{{first({value('RFP')})?['hrs_rfpid']}})"}), after=None)
    without = OrderedDict()
    f.add(without, "Audit", dv_create("hrs_runlogs", audit), after=None)
    f.add(a, "If_rfp", condition({"greater": [f"@length({value('RFP')})", 0]}, with_rfp, without))
    f.add(a, "Respond", hook_respond(f, {}))
    return f


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for flow in (receive_flow(), pipeline_flow(), gap_flow(), verify_flow(), approval_flow(),
                 status_tool_flow(), flag_tool_flow(), answer_tool_flow(), rerun_tool_flow(), pre_tool_hook_flow(), post_tool_hook_flow()):
        slug = flow.name.replace("RFP - ", "").lower().replace(" ", "-")
        (OUT / f"{slug}.json").write_text(json.dumps(flow.spec(), indent=2) + "\n", encoding="utf-8")
        print(f"{flow.name}: {len(json.dumps(flow.spec()))} bytes, env vars {sorted(flow.env_vars)}")


if __name__ == "__main__":
    main()
