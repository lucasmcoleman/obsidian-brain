"""Explicit DNS-rebinding / Host+Origin allowlist for streamable-HTTP (M-N).

The MCP SDK auto-enables transport security only when binding a loopback host;
this service binds 0.0.0.0 so a published host port can reach it, which silently
disables it. _transport_security lets an operator opt in via env, mirroring the
existing opt-in bearer-token pattern.
"""
import mcp_server


def test_transport_security_none_when_unset(monkeypatch):
    monkeypatch.delenv("BRAIN_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("BRAIN_ALLOWED_ORIGINS", raising=False)
    assert mcp_server._transport_security() is None


def test_transport_security_enabled_with_hosts(monkeypatch):
    monkeypatch.setenv("BRAIN_ALLOWED_HOSTS", "brain.local:8053, localhost:8053")
    monkeypatch.delenv("BRAIN_ALLOWED_ORIGINS", raising=False)

    ts = mcp_server._transport_security()

    assert ts is not None
    assert ts.enable_dns_rebinding_protection is True
    assert ts.allowed_hosts == ["brain.local:8053", "localhost:8053"]
    # Origins default to the http:// form of each host when not given explicitly.
    assert "http://brain.local:8053" in ts.allowed_origins


def test_transport_security_explicit_origins(monkeypatch):
    monkeypatch.setenv("BRAIN_ALLOWED_HOSTS", "h:8053")
    monkeypatch.setenv("BRAIN_ALLOWED_ORIGINS", "https://h, http://h:8053")

    ts = mcp_server._transport_security()

    assert ts.allowed_origins == ["https://h", "http://h:8053"]
