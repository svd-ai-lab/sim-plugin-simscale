from __future__ import annotations

import json
import urllib.error

import pytest

from sim_plugin_simscale.api import SimScaleApiError, SimScaleClient, SimScaleConfig


class FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self._data


class FakeErrorBody:
    def read(self):
        return b'{"message": "not allowed"}'

    def close(self):
        return None


def test_json_request_adds_api_key_and_body(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["method"] = req.get_method()
        captured["body"] = req.data
        captured["timeout"] = timeout
        return FakeResponse(b'{"ok": true}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = SimScaleClient(SimScaleConfig(api_key="secret", api_url="https://api.example.test", timeout_s=7))

    result = client.post("/projects", {"name": "demo"})

    assert result == {"ok": True}
    assert captured["url"] == "https://api.example.test/v0/projects"
    assert captured["headers"]["X-api-key"] == "secret"
    assert captured["method"] == "POST"
    assert json.loads(captured["body"].decode()) == {"name": "demo"}
    assert captured["timeout"] == 7


def test_http_error_normalizes_json_body(monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=FakeErrorBody(),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = SimScaleClient(SimScaleConfig(api_key="secret"))

    with pytest.raises(SimScaleApiError) as exc:
        client.get("/spaces")

    assert exc.value.status == 403
    assert str(exc.value) == "not allowed"
