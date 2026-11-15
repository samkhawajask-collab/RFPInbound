"""Regenerates the gallery entry's workflow files from solution/spec/flows, without a dev environment.

    python tools/flowgen/gallery.py [BASE]          # rewrite the files of flows whose spec changed since BASE (default HEAD)
    python tools/flowgen/gallery.py [BASE] --check  # report which files differ; exit 1 if any do

BASE is the commit whose specs the gallery files were last exported from or regenerated against.

Each file in gallery-submission/rfp-response-factory/solution/Workflows is a flow's clientdata as the solution export
writes it: the spec's clientdata with its placeholders resolved, the custom connector's logical name on its connection
reference, and a workflow canvas in the trigger's metadata (associatedData.graph). The canvas is generated, not saved
by the designer: the trigger and the top-level actions, an edge for each runAfter, a column per depth (README).

Placeholder values come from the existing gallery files, which were exported from dev: the connector's internal API
name and each prompt's msdyn_aimodel ID travel with the solution, so they don't change between exports. An action
whose spec is unchanged since BASE is copied from the export as it is; only changed and new actions are generated.
"""

import copy
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "solution" / "spec" / "flows"
WORKFLOWS = ROOT / "gallery-submission" / "rfp-response-factory" / "solution" / "Workflows"
TOOLS_PLACEHOLDER = "{{tools:api}}"
TOOLS_LOGICAL_NAME = "new_rfp-20factory-20tools"
PROMPT = re.compile(r"^\{\{prompt:(.+)\}\}$")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def gallery_files() -> dict[str, Path]:
    """Flow name -> gallery workflow file, from the name in each file's canvas."""
    out = {}
    for p in WORKFLOWS.glob("*.json"):
        trigger = next(iter(load(p)["properties"]["definition"]["triggers"].values()))
        out[trigger["metadata"]["associatedData"]["graph"]["name"]] = p
    return out


def placeholder_values(spec_clientdata, gallery_clientdata, found: dict) -> None:
    """Walks a spec and its exported copy side by side and records what each placeholder became."""
    if isinstance(spec_clientdata, dict) and isinstance(gallery_clientdata, dict):
        for k, v in spec_clientdata.items():
            if k == TOOLS_PLACEHOLDER:
                found[TOOLS_PLACEHOLDER] = next(g for g in gallery_clientdata if g.startswith("shared_new-"))
            elif k in gallery_clientdata:
                placeholder_values(v, gallery_clientdata[k], found)
    elif isinstance(spec_clientdata, list) and isinstance(gallery_clientdata, list):
        for a, b in zip(spec_clientdata, gallery_clientdata):
            placeholder_values(a, b, found)
    elif isinstance(spec_clientdata, str) and isinstance(gallery_clientdata, str):
        m = PROMPT.match(spec_clientdata)
        if m:
            found[spec_clientdata] = gallery_clientdata
        elif spec_clientdata == TOOLS_PLACEHOLDER and gallery_clientdata:
            found[TOOLS_PLACEHOLDER] = gallery_clientdata


def resolve(node, values: dict):
    if isinstance(node, dict):
        return {resolve(k, values): resolve(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve(v, values) for v in node]
    if isinstance(node, str):
        if node in values:
            return values[node]
        if TOOLS_PLACEHOLDER in node:
            return node.replace(TOOLS_PLACEHOLDER, values[TOOLS_PLACEHOLDER])
        if PROMPT.match(node):
            raise KeyError(f"No exported ID for {node}; export the solution from dev once to learn it")
    return node


def label(name: str) -> str:
    return name.replace("_", " ")


def node(action_name: str, action: dict) -> tuple[str, dict]:
    t = action["type"]
    if t in ("OpenApiConnection", "OpenApiConnectionWebhook"):
        host = action["inputs"]["host"]
        return "connector", {"apiName": host["connectionName"], "operationId": host["operationId"]}
    if t == "If":
        return "ifElse", {"expression": json.dumps(action["expression"])}
    if t == "Foreach":
        return "loop", {"expression": action["foreach"]}
    if t == "Until":
        return "loop", {"expression": action["expression"]}
    if t == "Response":
        return "end", {"runStatus": str(action["inputs"]["statusCode"])}
    if t == "Terminate":
        return "end", {"runStatus": action["inputs"]["runStatus"]}
    if t == "InitializeVariable":
        v = action["inputs"]["variables"][0]
        return "variable", {"name": v["name"], "type": v["type"]}
    if t in ("SetVariable", "AppendToArrayVariable"):
        return "variable", {"name": action["inputs"]["name"], "type": ""}
    if t == "Scope":
        return "scope", {"category": "Scope"}
    return "builtinFunction", {"category": t}


def graph(flow_name: str, definition: dict) -> dict:
    trigger_name, trigger = next(iter(definition["triggers"].items()))
    if trigger["type"] == "Request":
        start_config = {"triggerType": trigger.get("kind", "Request"), "connector": {"apiName": "", "operationId": ""}}
    else:
        host = trigger["inputs"]["host"]
        start_config = {"triggerType": trigger["type"], "connector": {"apiName": host["connectionName"], "operationId": host["operationId"]}}
    start_id = f"start-{trigger_name}"
    outcomes = [{"id": "default", "label": "Default"}]
    measured = {"width": 240, "height": 66}
    actions = definition["actions"]
    depth: dict[str, int] = {}

    def depth_of(name: str) -> int:
        if name not in depth:
            deps = actions[name].get("runAfter") or {}
            depth[name] = 1 + max((depth_of(d) for d in deps), default=0)
        return depth[name]

    rows: dict[int, int] = {}
    nodes = [{"id": start_id, "name": label(trigger_name), "type": "start", "version": 1, "position": {"x": 0, "y": 0},
              "data": {"config": start_config, "outcomes": outcomes}, "measured": measured}]
    edges = []
    for name, action in actions.items():
        col = depth_of(name)
        row = rows.get(col, 0)
        rows[col] = row + 1
        kind, config = node(name, action)
        nodes.append({"id": f"action-{name}", "name": label(name), "type": kind, "version": 1,
                      "position": {"x": 300 * col, "y": 110 * row}, "data": {"config": config, "outcomes": outcomes}, "measured": measured})
        sources = [f"action-{d}" for d in (action.get("runAfter") or {})] or [start_id]
        for s in sources:
            edges.append({"id": f"edge-{s}-action-{name}", "source": s, "target": f"action-{name}", "targetHandle": "input"})
    return {"name": flow_name, "nodes": nodes, "edges": edges}


CHILDREN = ("actions",)


def merge_actions(new: dict, base: dict, exported_base: dict, values: dict) -> dict:
    """The new spec's actions, in its order. An action unchanged since the base spec is copied from the export
    (the platform's own normalization: key order, and for agent flows a blank custom-connector apiId); a changed or
    new action is the spec's, resolved. A changed If/Scope/loop keeps its unchanged children from the export."""
    out = {}
    for name, action in new.items():
        old, old_export = base.get(name), exported_base.get(name)
        if old_export is not None and action == old:
            out[name] = copy.deepcopy(old_export)
            continue
        merged = resolve(copy.deepcopy(action), values)
        if old_export is not None and old is not None:
            if "actions" in action:
                merged["actions"] = merge_actions(action["actions"], old.get("actions", {}), old_export.get("actions", {}), values)
            if "else" in action:
                merged["else"]["actions"] = merge_actions(action["else"]["actions"], old.get("else", {}).get("actions", {}),
                                                          old_export.get("else", {}).get("actions", {}), values)
        out[name] = merged
    return out


def exported(spec: dict, base_spec: dict, base_export: dict, values: dict) -> dict:
    clientdata = resolve(copy.deepcopy(spec["clientdata"]), values)
    for ref in clientdata["properties"]["connectionReferences"].values():
        if ref["api"]["name"] == values.get(TOOLS_PLACEHOLDER):
            ref["api"]["logicalName"] = TOOLS_LOGICAL_NAME
    out = copy.deepcopy(base_export)
    out["properties"]["connectionReferences"] = clientdata["properties"]["connectionReferences"]
    definition, base_def = spec["clientdata"]["properties"]["definition"], base_spec["clientdata"]["properties"]["definition"]
    out_def = out["properties"]["definition"]
    out_def["parameters"] = clientdata["properties"]["definition"]["parameters"]
    # The trigger: the export's copy when only the canvas would change, else the spec's.
    (tname, trigger), (_, base_trigger) = next(iter(definition["triggers"].items())), next(iter(base_def["triggers"].items()))
    if trigger != base_trigger:
        out_def["triggers"] = {tname: {k: v for k, v in resolve(copy.deepcopy(trigger), values).items()}}
    out_def["actions"] = merge_actions(definition["actions"], base_def["actions"], base_export["properties"]["definition"]["actions"], values)
    next(iter(out_def["triggers"].values()))["metadata"] = {"associatedData": {"graph": graph(spec["name"], out_def)}}
    return out


def git_show(path: Path, ref: str):
    import subprocess
    rel = path.relative_to(ROOT).as_posix()
    return json.loads(subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=ROOT, capture_output=True, check=True,
                                     encoding="utf-8").stdout)


def main(check: bool, base_ref: str) -> int:
    """base_ref: the commit whose specs the gallery files were exported from."""
    files = gallery_files()
    values: dict = {}
    base = {p: git_show(p, base_ref) for p in SPECS.glob("*.json")}
    for p, spec in base.items():
        placeholder_values(spec["clientdata"], load(files[spec["name"]]), values)
    differ = 0
    for p in sorted(SPECS.glob("*.json")):
        spec = load(p)
        if spec == base[p]:
            continue
        target = files[spec["name"]]
        text = json.dumps(exported(spec, base[p], load(target), values), ensure_ascii=False, separators=(",", ":"))
        if target.read_text(encoding="utf-8") != text:
            differ += 1
            print(f"{'differs' if check else 'wrote'}: {target.name}")
            if not check:
                target.write_text(text, encoding="utf-8", newline="")
    print(f"{differ} of {len(files)} workflow files {'differ' if check else 'rewritten'} (specs changed since {base_ref})")
    return 1 if check and differ else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main("--check" in sys.argv, args[0] if args else "HEAD"))
