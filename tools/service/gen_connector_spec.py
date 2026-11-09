"""Generates the custom connector's OpenAPI definition from the service's route table.

    python tools/service/gen_connector_spec.py

Keeps solution/spec/connector/rfp-tools.openapi.json in step with function_app.ROUTES, so a new
operation is a new route plus a regeneration - the two can never drift.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import function_app  # noqa: E402

OUT = HERE.parents[1] / "solution" / "spec" / "connector" / "rfp-tools.openapi.json"


def main() -> None:
    paths = {}
    for route, handler in function_app.ROUTES.items():
        paths[f"/{route}"] = {"post": {
            "operationId": handler.operation_id, "summary": handler.summary,
            "description": handler.summary + ". See tools/service/function_app.py.",
            "consumes": ["application/json"], "produces": ["application/json"],
            "parameters": [{"name": "body", "in": "body", "required": True, "schema": {"type": "object"}}],
            "responses": {"200": {"description": "OK", "schema": {"type": "object"}}, "400": {"description": "Invalid input"}},
        }}
    spec = {
        "swagger": "2.0",
        "info": {"title": "RFP Factory Tools", "version": "1.0", "contact": {"name": "RFP Factory solution team"},
                 "description": "Deterministic operations for the RFP Response Factory (ADR-0013, ADR-0016, ADR-0017)."},
        "host": "@environmentVariables(\"hrs_RfpToolsBaseUrl\")",
        "basePath": "/api",
        "schemes": ["https"],
        "securityDefinitions": {"api_key": {"type": "apiKey", "in": "query", "name": "code"}},
        "security": [{"api_key": []}],
        "x-ms-connector-metadata": [
            {"propertyName": "Website", "propertyValue": "https://learn.microsoft.com/power-platform/"},
            {"propertyName": "Privacy policy", "propertyValue": "https://privacy.microsoft.com/"},
            {"propertyName": "Categories", "propertyValue": "Productivity"},
        ],
        "paths": paths,
    }
    OUT.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    print(f"{len(paths)} operations -> {OUT}")


if __name__ == "__main__":
    main()
