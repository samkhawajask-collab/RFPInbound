"""Document Intelligence calls back off on throttling and give up as a TransientError (ARCHITECTURE.md 14.4, ADR-0026)."""

import email.message
import io
import urllib.error

import pytest

from rfp_tools import pdf_layout
from rfp_tools.pdf_layout import TransientError, _request


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://di.example/analyze", code, "error", headers, io.BytesIO(b""))


def _urlopen_sequence(monkeypatch, outcomes):
    calls = []

    def fake(req, timeout):
        calls.append(req.full_url)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(pdf_layout.urllib.request, "urlopen", fake)
    return calls


def test_429_retries_with_retry_after_then_succeeds(monkeypatch):
    calls = _urlopen_sequence(monkeypatch, [_http_error(429, "7"), _http_error(503), "ok"])
    slept = []
    assert _request("GET", "https://di.example/op", "t", sleep=slept.append) == "ok"
    assert len(calls) == 3 and slept == [7.0, 4.0]


def test_gives_up_as_transient_after_three_retries(monkeypatch):
    _urlopen_sequence(monkeypatch, [_http_error(429)] * 4)
    slept = []
    with pytest.raises(TransientError):
        _request("GET", "https://di.example/op", "t", sleep=slept.append)
    assert slept == [2.0, 4.0, 8.0]


def test_client_errors_are_not_retried(monkeypatch):
    calls = _urlopen_sequence(monkeypatch, [_http_error(400)])
    with pytest.raises(urllib.error.HTTPError):
        _request("GET", "https://di.example/op", "t", sleep=lambda s: None)
    assert len(calls) == 1


def test_network_errors_retry(monkeypatch):
    _urlopen_sequence(monkeypatch, [urllib.error.URLError("reset"), "ok"])
    assert _request("GET", "https://di.example/op", "t", sleep=lambda s: None) == "ok"
