"""Authenticated end-to-end source -> review -> attention HTTP contract."""
from datetime import date, timedelta

import pytest
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient

from auth import BearerAuthMiddleware
from workspace_api import register_workspace


@pytest.fixture
def http(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_AUTH_TOKEN", "unit-token")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Project.md").write_text("---\ntype: project-note\ndate: 2026-06-10\n---\n# Project\n- [ ] Deliver RFQ-2611 report\n")
    mcp = FastMCP("workspace-test", stateless_http=True)
    work = register_workspace(mcp, str(vault))
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, token="unit-token")
    return TestClient(app), work, {"Authorization": "Bearer unit-token"}


def test_source_review_attention_roundtrip(http):
    client, work, headers = http
    assert client.get("/ui/api/workspace").status_code == 401
    assert client.post("/ui/api/sync", headers=headers, json={}).json()["sources"] == 1
    r = next(r for r in client.get("/ui/api/records", headers=headers).json()["records"] if r["kind"] == "commitment")
    assert not client.get("/ui/api/workspace", headers=headers).json()["items"]
    accepted = client.post("/ui/api/review", headers=headers, json={"id": r["id"], "version": r["version"], "action": "accept", "fields": {"owner": "Jordan", "due": (date.today() + timedelta(days=1)).isoformat()}})
    assert accepted.status_code == 200
    assert client.get("/ui/api/workspace", headers=headers).json()["items"][0]["owner"] == "Jordan"
    source = client.get("/ui/api/source", params={"record": r["id"]}, headers=headers).json()
    assert "RFQ-2611" in source["evidence"]["quote"]
    assert client.get("/ui/api/lookup?q=RFQ-2611", headers=headers).json()["results"]
    assert client.get("/ui/api/workspace", headers=headers).headers["cache-control"] == "no-store"


def test_bad_requests_and_stale_versions_do_not_write(http):
    client, work, headers = http
    client.post("/ui/api/sync", headers=headers, json={})
    for payload in ([], {"id": [], "action": "accept", "version": 1}):
        assert client.post("/ui/api/review", headers=headers, json=payload).status_code == 400
    assert client.post("/ui/api/records", headers=headers, json={"kind": [], "title": "Bad"}).status_code == 400
    r = work.records()[0]
    request = {"id": r["id"], "action": "accept", "version": r["version"]}
    assert client.post("/ui/api/review", headers=headers, json=request).status_code == 200
    assert client.post("/ui/api/review", headers=headers, json=request).status_code == 409


def test_no_token_disables_mutations_even_without_middleware(http, monkeypatch):
    client, work, headers = http
    monkeypatch.delenv("BRAIN_AUTH_TOKEN")
    assert client.post("/ui/api/sync", headers=headers, json={}).status_code == 503
    assert not work.record_dir.exists()
