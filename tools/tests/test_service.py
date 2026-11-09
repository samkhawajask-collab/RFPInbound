"""The HTTP layer translates JSON faithfully; logic is tested in the module tests."""

import base64
import json
import sys

import azure.functions as func
import pytest

from tests.conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools" / "service"))
import function_app  # noqa: E402

RFP_A = REPO_ROOT / "data" / "RFP_Synthetic_Dataset_Pilot" / "01_Inbound_RFPs" / "RFP-A_Cedar_Valley_Water_Authority_RFP-2026-117.docx"


def call(operation: str, body: dict) -> tuple[int, dict]:
    req = func.HttpRequest(method="POST", url=f"/api/{operation}", route_params={"operation": operation},
                           body=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    resp = function_app.dispatch.build().get_user_function()(req)
    return resp.status_code, json.loads(resp.get_body())


def test_extract_requirements_from_rfp_a():
    status, out = call("extract-requirements", {"contentBase64": base64.b64encode(RFP_A.read_bytes()).decode(),
                                                "idPrefix": "CV", "expectedMin": 40, "expectedMax": 80})
    assert status == 200
    assert len(out["requirements"]) == 60 and out["gateG2"]["passed"]


def test_derive_compliance_batch():
    status, out = call("derive-compliance", {"requirements": [
        {"reqId": "X-1", "hasApprovedAnswer": True, "firedSeverities": ["Warn"], "inContradictionGroup": True},
        {"reqId": "X-2", "hasApprovedAnswer": False}]})
    assert status == 200
    assert [r["compliance"] for r in out["results"]] == ["Clarify", "Gap"]


def test_match_rules_returns_threshold_evidence():
    status, out = call("match-rules", {"requirements": [{"reqId": "X-1", "text": "Availability shall be 99.95% monthly."}],
                                       "rules": [{"ruleId": "R-02", "triggerPattern": "availability above 99.9%",
                                                  "metric": "availability_percent", "operator": "gt", "value": 99.9}]})
    assert status == 200
    cand = out["results"][0]["candidates"][0]
    assert cand["rule_id"] == "R-02" and cand["threshold"]["breached"] is True


def test_bad_input_is_400_not_500():
    status, out = call("derive-compliance", {"requirements": [{"reqId": "X"}]})
    assert status == 400 and "error" in out


def test_unknown_operation_is_404():
    assert call("nope", {})[0] == 404


def test_transient_failure_is_503_and_fault_injection_counts(monkeypatch):
    """Drill (a): RFPTOOLS_FAULTS fails the first n calls of one operation with a retryable 503 (ADR-0026)."""
    monkeypatch.setenv("RFPTOOLS_FAULTS", "derive-compliance:2")
    monkeypatch.setattr(function_app, "_fault_calls", {})
    body = {"requirements": [{"reqId": "X-1", "hasApprovedAnswer": True}]}
    assert [call("derive-compliance", body)[0] for _ in range(3)] == [503, 503, 200]
    assert call("match-rules", {"requirements": [], "rules": []})[0] == 200
