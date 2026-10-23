"""Run a verified Porter v2 handoff through an installed MCP server.

Requires an initialized disposable NERODE_DSN, TRUNKIT_ALLOW_WRITE=1 and --write.
Appends uniquely named demo policy/trace/session rows. No Porter internals are
imported. The same script is used by installed-wheel Muse CI.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import psycopg
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from smoke_muse import console_script, require


async def demonstrate() -> None:
    for name in ("calx", "nerode", "trunkit_mcp"):
        module = importlib.import_module(name)
        require(
            Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()),
            f"{name} must come from an installed package, not an editable checkout",
        )
    session = "porter-demo-" + uuid.uuid4().hex
    receiver, tool = session + "-receiver", session + "-publish"
    # Public setup SQL supplies a policy and a prior trace. The handoff itself
    # below uses only MCP tools, including payload and ledger creation.
    with psycopg.connect(os.environ["NERODE_DSN"], connect_timeout=5) as conn:
        conn.execute(
            "SELECT nerode.register_policy(%s,%s,%s,'Reservation required','block')",
            (tool, tool, "($1->'reservation'->>'permitted')::boolean"),
        )
        for symbol in "A_____":
            conn.execute("SELECT nerode.log_cybernetic(%s,'control',%s)", (session, symbol))
    env = {
        key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "TRUNKIT_SRC")
    }
    with tempfile.TemporaryDirectory(prefix="porter-mcp-demo-") as cwd:
        parameters = StdioServerParameters(command=console_script("trunkit-mcp"), env=env, cwd=cwd)
        async with stdio_client(parameters) as streams, ClientSession(*streams) as client:
            await client.initialize()

            async def call(name: str, arguments: dict, *, expected_error: bool = False) -> dict:
                result = await client.call_tool(name, arguments)
                require(not result.isError, f"{name} failed at the MCP transport: {result}")
                output = result.structuredContent or json.loads(result.content[0].text)
                require(expected_error or "error" not in output, f"{name} failed: {output}")
                return output

            envelope = await call(
                "porter_close",
                {
                    "session_id": session,
                    "mode": "claim_once",
                    "payloads_json": '{"task":{"objective":"Review the release"}}',
                    "ledger_entries_json": json.dumps(
                        [
                            {
                                "path": "reservation",
                                "value": {"permitted": False},
                                "schema_type": "reservation",
                            }
                        ]
                    ),
                },
            )
            require(envelope.get("envelope_v") == 2, f"Expected v2 envelope: {envelope}")
            serialized = json.dumps(envelope)
            audit = await call("porter_open", {"envelope_json": serialized})
            require(
                audit.get("verification") == "valid" and audit.get("resolved") == {},
                f"Manifest-only open failed: {audit}",
            )
            require(
                any(w["detector"] == "dead_time_5" for w in audit["health"]["warnings"]),
                "Prior-session dead-time warning missing",
            )
            lease = await call(
                "porter_claim",
                {
                    "envelope_json": serialized,
                    "receiver_session": receiver,
                },
            )
            require(lease.get("claimed") is True, f"Claim failed: {lease}")
            access = {
                "envelope_json": serialized,
                "receiver_session": receiver,
                "claim_token": lease["claim_token"],
            }
            selected = await call("porter_open", {**access, "payload_keys_json": '["task"]'})
            require(
                selected.get("resolved") == {"task": {"objective": "Review the release"}},
                f"Selected payload failed: {selected}",
            )
            bounded = await call(
                "porter_open",
                {
                    **access,
                    "payload_keys_json": '["task"]',
                    "max_payload_bytes": 1,
                },
                expected_error=True,
            )
            require(
                bounded.get("payload_budget_exceeded") is True and not bounded["resolved"],
                f"Payload budget failed: {bounded}",
            )
            tampered = dict(envelope, attention_hint="forged")
            rejected = await call(
                "porter_open",
                {
                    "envelope_json": json.dumps(tampered),
                },
                expected_error=True,
            )
            require(
                rejected.get("verification") == "refuted" and not rejected["resolved"],
                f"Tampered envelope accepted: {rejected}",
            )
            gate = await call("porter_open_and_gate", {**access, "tool": tool})
            require(
                gate.get("decision", {}).get("verdict") == "BLOCK",
                f"Carried reservation did not block first action: {gate}",
            )
            released = await call(
                "porter_release",
                {
                    "envelope_json": serialized,
                    "claim_token": lease["claim_token"],
                },
            )
            require(released.get("released") is True, f"Lease release failed: {released}")
    print(
        "Porter v2 MCP: manifest and payload verified; tamper and byte budget rejected; "
        "claim/release verified; first action BLOCK; dead_time_5 health warning present"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="append this demo to the explicit test DB"
    )
    args = parser.parse_args()
    require(args.write, "This demo writes rows; pass --write to run it")
    require(
        bool(os.environ.get("NERODE_DSN")), "Set NERODE_DSN to an initialized disposable database"
    )
    require(os.environ.get("TRUNKIT_ALLOW_WRITE") == "1", "Set TRUNKIT_ALLOW_WRITE=1 explicitly")
    asyncio.run(asyncio.wait_for(demonstrate(), timeout=120))


if __name__ == "__main__":
    main()
