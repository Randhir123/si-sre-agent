import json

import requests

from tools import ibm_logs


class FakeResponse:
    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self, decode_unicode=False):
        if decode_unicode:
            return [
                line.decode("utf-8") if isinstance(line, bytes) else line
                for line in self._lines
            ]
        return self._lines


def test_resolve_endpoint_converts_ingress_host(monkeypatch):
    monkeypatch.setenv(
        "IBM_LOGS_ENDPOINT",
        "https://example.ingress.us-south.logs.cloud.ibm.com/",
    )

    assert (
        ibm_logs._resolve_endpoint()
        == "https://example.api.us-south.logs.cloud.ibm.com"
    )


def test_parse_streaming_response_extracts_text_message():
    record = {
        "result": {
            "results": [
                {
                    "metadata": [{"key": "timestamp", "value": "2026-05-30T09:00:00Z"}],
                    "user_data": json.dumps({"text": {"message": "expected log line"}}),
                }
            ]
        }
    }
    response = FakeResponse([json.dumps(record).encode()])

    assert ibm_logs._parse_streaming_response(response, limit=1) == [
        ("2026-05-30T09:00:00Z", "expected log line")
    ]


def test_query_logs_reports_iam_network_error(monkeypatch):
    monkeypatch.setenv("IBM_CLOUD_API_KEY", "test-key")
    monkeypatch.setenv(
        "IBM_LOGS_ENDPOINT",
        "https://example.api.us-south.logs.cloud.ibm.com",
    )
    monkeypatch.setitem(ibm_logs._token_cache, "token", None)
    monkeypatch.setitem(ibm_logs._token_cache, "expires_at", 0.0)

    def fail_post(*args, **kwargs):
        raise requests.ConnectionError("IAM unavailable")

    monkeypatch.setattr(requests, "post", fail_post)

    assert ibm_logs.query_logs("*", "si") == "[IAM network error] IAM unavailable"
