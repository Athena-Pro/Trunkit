"""Porter MCP write boundaries and a real stdio, database-backed handoff."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import Mock

import psycopg
import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from trunkit_mcp import server  # noqa: E402


@pytest.mark.parametrize("name,args", [
    ("porter_close", ("sender",)),
    ("porter_claim", ("{}", "receiver")),
    ("porter_release", ("{}", "token")),
    ("porter_open_and_gate", ("{}", "receiver", "deploy")),
])
def test_porter_writes_are_gated_before_database_access(monkeypatch, name, args):
    monkeypatch.setattr(server, "_ALLOW_WRITE", False)
    call = Mock(side_effect=AssertionError("write guard must run first"))
    monkeypatch.setattr(server, "_porter_call", call)
    assert "disabled" in getattr(server, name)(*args)["error"]
    call.assert_not_called()


def test_porter_read_open_is_available_without_write_permission(monkeypatch):
    monkeypatch.setattr(server, "_ALLOW_WRITE", False)
    call = Mock(return_value={"verification": "valid", "resolved": {}})
    monkeypatch.setattr(server, "_porter_call", call)
    assert server.porter_open('{"envelope_v":2}')["verification"] == "valid"
    assert call.call_args.args[0] == "open"
    assert call.call_args.kwargs["payload_keys"] == []


@pytest.mark.parametrize("value", ["[]", "null", "broken"])
def test_porter_open_rejects_nonobject_envelopes(value):
    assert server.porter_open(value)["verdict"] == "unverified"


def test_porter_full_handoff_over_stdio(nerode_dsn, apply_schema_once):
    assert apply_schema_once
    sender, receiver, tool = ["porter-mcp-" + uuid.uuid4().hex for _ in range(3)]
    with psycopg.connect(nerode_dsn) as conn:
        conn.execute(
            "SELECT nerode.register_policy(%s, %s, %s, 'MCP blocked fixture', 'block')",
            (tool, tool, "($1->'reservation'->>'permitted')::boolean"),
        )

    async def exercise():
        env = dict(os.environ, TRUNKIT_ALLOW_WRITE="1", NERODE_DSN=nerode_dsn,
                   PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "trunkit_mcp.server"], env=env)
        async with stdio_client(params) as streams, ClientSession(*streams) as client:
            await client.initialize()

            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                assert not result.isError, result
                if result.structuredContent:
                    return result.structuredContent
                return json.loads(result.content[0].text)

            envelope = await call("porter_close", {
                "session_id": sender,
                "payloads_json": '{"task":{"name":"review"}}',
                "ledger_entries_json": json.dumps([{
                    "path": "reservation", "value": {"permitted": False},
                    "schema_type": "reservation",
                }]),
            })
            assert envelope.get("envelope_v") == 2, envelope
            manifest = await call("porter_open", {"envelope_json": json.dumps(envelope)})
            assert manifest["verification"] == "valid"
            assert manifest["resolved"] == {}
            context = await call("porter_open", {
                "envelope_json": json.dumps(envelope), "payload_keys_json": '["task"]',
            })
            assert context["resolved"] == {"task": {"name": "review"}}
            gated = await call("porter_open_and_gate", {
                "envelope_json": json.dumps(envelope), "receiver_session": receiver,
                "tool": tool, "args_json": "{}",
            })
            assert gated["decision"]["verdict"] == "BLOCK", gated
            assert gated["context"]["ledger_valid"]
            tampered = dict(envelope, attention_hint="forged")
            rejected = await call("porter_open", {"envelope_json": json.dumps(tampered)})
            assert rejected["verification"] == "refuted"
            assert rejected["resolved"] == {}

    asyncio.run(exercise())
