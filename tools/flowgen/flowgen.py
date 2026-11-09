"""Builders for Power Automate / agent flow definitions (workflow clientdata), used by build.py.

Shapes are copied from flows that work in the dev environment (Dataverse, Teams, child flows),
from the connectors' published OpenAPI definitions (SharePoint, Word Online), and from the M0 spike
(prompts, ADR-0015). Placeholders resolved by scripts/apply-flows.ps1 at apply time:

    {{prompt:<name>}}   msdyn_aimodel ID of a published prompt
    {{tools:api}}       internal API name of the rfp-tools custom connector

Environment variables are referenced as flow parameters, so no environment value is in source.
"""

import json
from collections import OrderedDict

DV = "shared_commondataserviceforapps"
SP = "shared_sharepointonline"
WORD = "shared_wordonlinebusiness"
TEAMS = "shared_teams"
TOOLS = "{{tools:api}}"

CONNECTION_REFERENCES = {
    DV: "hrs_dataverseconnection",
    SP: "hrs_sharepointconnection",
    WORD: "hrs_wordonlineconnection",
    TEAMS: "hrs_teamsconnection",
    TOOLS: "hrs_rfptoolsconnection",
}


def env(name: str) -> str:
    """Expression for an environment variable value."""
    return f"@parameters('{name} ({name})')"


def env_inline(name: str) -> str:
    return f"parameters('{name} ({name})')"


class Flow:
    def __init__(self, name: str, description: str, agent: bool):
        self.name, self.description, self.agent = name, description, agent
        self.trigger: dict = {}
        self.actions: OrderedDict = OrderedDict()
        self.env_vars: set[str] = set()
        self.apis: set[str] = set()

    # -- composition --------------------------------------------------------------------------
    def add(self, container: OrderedDict, name: str, action: dict, after: str | list | None = "auto") -> str:
        """Append an action; by default it runs after the previous action in the same container."""
        if after == "auto":
            after = next(reversed(container)) if container else None
        if after is None:
            action["runAfter"] = {}
        elif isinstance(after, list):
            action["runAfter"] = {a: ["Succeeded"] for a in after}
        else:
            action["runAfter"] = {after: ["Succeeded"]}
        container[name] = action
        self._scan(action)
        return name

    def _scan(self, node):
        text = json.dumps(node)
        for marker in ("parameters('",):
            start = 0
            while (i := text.find(marker, start)) != -1:
                end = text.find(" (", i)
                self.env_vars.add(text[i + len(marker):end])
                start = i + 1
        for api in CONNECTION_REFERENCES:
            if f'"connectionName": "{api}"' in text:
                self.apis.add(api)

    # -- output -------------------------------------------------------------------------------
    def clientdata(self) -> dict:
        params = OrderedDict([
            ("$connections", {"defaultValue": {}, "type": "Object"}),
            ("$authentication", {"defaultValue": {}, "type": "SecureObject"}),
        ])
        for name in sorted(self.env_vars):
            params[f"{name} ({name})"] = {"defaultValue": "", "type": "String", "metadata": {"schemaName": name}}
        refs = OrderedDict()
        for api in sorted(self.apis):
            refs[api] = {"runtimeSource": "embedded", "connection": {"connectionReferenceLogicalName": CONNECTION_REFERENCES[api]},
                         "api": {"name": api}}
        return {
            "properties": {
                "connectionReferences": refs,
                "definition": {
                    "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
                    "contentVersion": "1.0.0.0",
                    "parameters": params,
                    "triggers": self.trigger,
                    "actions": self.actions,
                    "outputs": {},
                },
                "templateName": "",
            },
            "schemaVersion": "1.0.0.0",
        }

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "modernflowtype": 1 if self.agent else 0,
                "environmentVariables": sorted(self.env_vars), "clientdata": self.clientdata()}


# --- generic actions --------------------------------------------------------------------------

def connector(api: str, operation: str, parameters: dict, webhook: bool = False, timeout: str | None = None) -> dict:
    api_id = f"/providers/Microsoft.PowerApps/apis/{api}"
    action = {"type": "OpenApiConnectionWebhook" if webhook else "OpenApiConnection",
              "inputs": {"host": {"apiId": api_id, "operationId": operation, "connectionName": api}, "parameters": parameters}}
    if timeout:
        action["limit"] = {"timeout": timeout}
    return action


def compose(value) -> dict:
    return {"type": "Compose", "inputs": value}


def scope(actions: OrderedDict) -> dict:
    return {"type": "Scope", "actions": actions}


def condition(expression: dict, if_true: OrderedDict, if_false: OrderedDict | None = None) -> dict:
    action = {"type": "If", "expression": expression, "actions": if_true}
    if if_false is not None:
        action["else"] = {"actions": if_false}
    return action


def foreach(items: str, actions: OrderedDict, concurrency: int = 20) -> dict:
    action = {"type": "Foreach", "foreach": items, "actions": actions}
    action["runtimeConfiguration"] = {"concurrency": {"repetitions": concurrency}}
    return action


def terminate(status: str, message: str = "") -> dict:
    inputs = {"runStatus": status}
    if status == "Failed":
        inputs["runError"] = {"code": "PipelineStopped", "message": message}
    return {"type": "Terminate", "inputs": inputs}


def init_variable(name: str, kind: str, value) -> dict:
    return {"type": "InitializeVariable", "inputs": {"variables": [{"name": name, "type": kind, "value": value}]}}


def append_variable(name: str, value) -> dict:
    return {"type": "AppendToArrayVariable", "inputs": {"name": name, "value": value}}


def set_variable(name: str, value) -> dict:
    return {"type": "SetVariable", "inputs": {"name": name, "value": value}}


# --- Dataverse ---------------------------------------------------------------------------------

def dv_list(entity_set: str, select: str | None = None, filter_: str | None = None, expand: str | None = None,
            orderby: str | None = None, top: int | None = None) -> dict:
    p = {"entityName": entity_set}
    if select:
        p["$select"] = select
    if filter_:
        p["$filter"] = filter_
    if expand:
        p["$expand"] = expand
    if orderby:
        p["$orderby"] = orderby
    if top:
        p["$top"] = top
    action = connector(DV, "ListRecords", p)
    action["runtimeConfiguration"] = {"paginationPolicy": {"minimumItemCount": 5000}}
    return action


def dv_get(entity_set: str, record_id: str, select: str | None = None, expand: str | None = None) -> dict:
    p = {"entityName": entity_set, "recordId": record_id}
    if select:
        p["$select"] = select
    if expand:
        p["$expand"] = expand
    return connector(DV, "GetItem", p)


def dv_create(entity_set: str, fields: dict) -> dict:
    return connector(DV, "CreateRecord", {"entityName": entity_set, **{f"item/{k}": v for k, v in fields.items()}})


def dv_update(entity_set: str, record_id: str, fields: dict) -> dict:
    return connector(DV, "UpdateOnlyRecord", {"entityName": entity_set, "recordId": record_id, **{f"item/{k}": v for k, v in fields.items()}})


def dv_delete(entity_set: str, record_id: str) -> dict:
    return connector(DV, "DeleteRecord", {"entityName": entity_set, "recordId": record_id})


def dv_create_from(entity_set: str, item_expr: str, columns: list[str]) -> dict:
    """CreateRecord whose column values come from an object (e.g. items('loop')) with matching keys."""
    return dv_create(entity_set, {c: f"@{item_expr}?['{c}']" for c in columns})


def dv_trigger(entity: str, message: int, filtering: str | None = None, conditions: list[str] | None = None,
               single_run: bool = False) -> dict:
    """Dataverse 'When a row is added, modified or deleted'. message: 1 create, 3 update, 4 create or update."""
    p = {"subscriptionRequest/message": message, "subscriptionRequest/entityname": entity,
         "subscriptionRequest/scope": 4, "subscriptionRequest/runas": 1}
    if filtering:
        p["subscriptionRequest/filteringattributes"] = filtering
    trigger = connector(DV, "SubscribeWebhookTrigger", p, webhook=True)
    if conditions:
        trigger["conditions"] = [{"expression": c} for c in conditions]
    if single_run:
        # Only the pipeline needs one run at a time per environment; human-loop flows must run in parallel.
        trigger["runtimeConfiguration"] = {"concurrency": {"runs": 1}}
    return {"When_a_row_changes": trigger}


# --- rfp-tools and prompts ----------------------------------------------------------------------

def tools(operation: str, body) -> dict:
    action = connector(TOOLS, operation, {"body": body})
    action["limit"] = {"timeout": "PT10M"}
    # 503 (throttled Document Intelligence, an injected fault) and 5xx are retried; 400 (bad input) is not (ADR-0026).
    action["retryPolicy"] = {"type": "exponential", "count": 3, "interval": "PT10S"}
    return action


def prompt(name: str, inputs: dict) -> dict:
    p = {"recordId": f"{{{{prompt:{name}}}}}", **{f"item/requestv2/{k}": v for k, v in inputs.items()}}
    action = {"type": "OpenApiConnection", "inputs": {"host": {
        "apiId": "providers/Microsoft.ProcessSimple/operationGroups/aibuilder", "operationId": "aibuilderpredict_customprompt",
        "connectionName": DV}, "parameters": p}}
    action["retryPolicy"] = {"type": "exponential", "count": 3, "interval": "PT20S"}
    return action


def prompt_output(action_name: str) -> str:
    """The prompt's JSON output. When the model adds a property the schema doesn't allow, AI Builder drops the
    structured output but still returns valid JSON as text, so the text is parsed instead; rfp-tools validates
    the content either way."""
    out = f"outputs('{action_name}')?['body/responsev2/predictionOutput']"
    return f"if(equals({out}?['structuredOutput'], null), json({out}?['text']), {out}?['structuredOutput'])"


# --- SharePoint, Word, Teams --------------------------------------------------------------------

def sp_get_content_by_path(path_expr: str) -> dict:
    return connector(SP, "GetFileContentByPath", {"dataset": env("hrs_SharePointSiteUrl"), "path": path_expr, "inferContentType": False})


def sp_create_file(folder_expr: str, name_expr: str, content_expr: str) -> dict:
    return connector(SP, "CreateFile", {"dataset": env("hrs_SharePointSiteUrl"), "folderPath": folder_expr,
                                        "name": name_expr, "body": content_expr})


def word_to_pdf(file_path_expr: str) -> dict:
    return connector(WORD, "GetFilePDF", {"source": env("hrs_SharePointSiteUrl"), "drive": env("hrs_DocumentDriveId"), "file": file_path_expr})


def teams_card_and_wait(recipient_expr: str, card_expr: str, update_message: str, timeout: str) -> dict:
    return connector(TEAMS, "PostCardAndWaitForResponse", {
        "poster": "Flow bot", "location": "Chat with Flow bot",
        "body/body/messageBody": card_expr, "body/body/updateMessage": update_message,
        "body/body/recipient/to": recipient_expr}, webhook=True, timeout=timeout)


def teams_message(recipient_expr: str, html_expr: str) -> dict:
    return connector(TEAMS, "PostMessageToConversation", {
        "poster": "Flow bot", "location": "Chat with Flow bot", "body/recipient": recipient_expr, "body/messageBody": html_expr})
