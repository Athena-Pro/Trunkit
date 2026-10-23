"""Smoke a wheel installed with [muse], using its real console scripts and MCP stdio.

Run this with the fresh venv's Python, outside the checkout. No pytest/dev extras
are needed. --database expects initialized schemas, generated arithmetic data,
and a recorded certificate for seeded claim 2 in a dedicated test database.
It performs consumer calls only; setup and certificate recording are explicit.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import subprocess
import sys
import sysconfig
import tempfile
from importlib.metadata import version
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from psycopg import pq


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def console_script(name: str) -> str:
    suffix = ".exe" if os.name == "nt" else ""
    script = Path(sysconfig.get_path("scripts")) / f"{name}{suffix}"
    require(script.is_file(), f"Installed console script missing: {script}")
    return str(script)


def merkle_root(leaves: list[str]) -> str:
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    level = [digest(leaf) for leaf in leaves] or [digest("")]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [digest(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


async def call(session: ClientSession, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments)
    require(not result.isError, f"{name} transport/tool error: {result}")
    payload = getattr(result, "structuredContent", None)
    if payload is None:
        texts = [item.text for item in result.content if item.type == "text"]
        require(len(texts) == 1, f"{name}: expected one JSON result, received {result}")
        payload = json.loads(texts[0])
    require(isinstance(payload, dict), f"{name}: unexpected result {payload!r}")
    require("error" not in payload, f"{name} failed: {payload}")
    return payload


async def smoke(database: bool, cwd: str, env: dict[str, str]) -> None:
    params = StdioServerParameters(command=console_script("trunkit-mcp"), env=env, cwd=cwd)
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        listing = await session.list_tools()
        names = {tool.name for tool in listing.tools}
        required = {
            "kernel_verify", "commitment_verify", "arith_verify", "standing",
            "claim_verify", "claim_export", "bundle_verify", "ledger_chain",
            "porter_open", "porter_close", "porter_claim", "porter_release",
            "porter_open_and_gate",
        }
        require(required <= names, f"Missing MCP tools: {required - names}")
        print(f"MCP stdio discovery: {len(names)} tools")

        out = await call(session, "kernel_verify", {"witness_json": json.dumps({
            "schema": "factorization", "n": 28, "factors": [[2, 2], [7, 1]],
        })})
        require(out.get("verdict") == "valid", f"Kernel verification failed: {out}")

        # These three JSON strings used to be pre-parsed into lists/dicts by
        # FastMCP because the server annotated them Optional[str].
        leaves = ["alpha", "beta"]
        out = await call(session, "commitment_verify", {
            "root": merkle_root(leaves), "leaves_json": json.dumps(leaves),
        })
        require(out.get("verdict") == "valid", f"leaves_json failed: {out}")
        out = await call(session, "commitment_verify", {
            "root": merkle_root(leaves), "leaves_json": json.dumps(["tampered", "beta"]),
        })
        require(out.get("verdict") == "refuted", f"Tampered commitment accepted: {out}")
        claim = {"claim_id": 2, "statement": "28 is perfect", "method": "comp_sql",
                 "status": "valid", "evidence": "{}"}
        out = await call(session, "commitment_verify", {
            "root": merkle_root(["claim:2", "stmt:28 is perfect", "method:comp_sql",
                                 "status:valid", "evid:{}"]),
            "claim_json": json.dumps(claim),
        })
        require(out.get("verdict") == "valid", f"claim_json failed: {out}")
        phi = {"op": "Divides", "args": [
            {"op": "Const", "args": [7]}, {"op": "Const", "args": [28]},
            {"op": "Const", "args": [4]},
        ]}
        for interp in ({}, {"interp_json": "{}"}):
            out = await call(session, "arith_verify", {"phi_json": json.dumps(phi), **interp})
            require(out.get("verdict") == "valid", f"Arithmetic verification failed: {out}")
        print("MCP kernel and all three optional JSON-string arguments: valid")

        if database:
            out = await call(session, "standing", {"limit": 100})
            require(out.get("total_matched", 0) > 0, f"No seeded claims: {out}")
            out = await call(session, "claim_verify", {"claim_id": 2})
            require(out.get("verdict") == "valid", f"Generated-data claim failed: {out}")
            out = await call(session, "claim_export", {"claim_ids": [2]})
            bundle = out.get("bundle")
            require(isinstance(bundle, dict) and bool(bundle.get("claims")),
                    f"No certified claim in exported bundle: {out}")
            bundle_json = json.dumps(bundle)
            out = await call(session, "ledger_chain", {"bundle_json": bundle_json})
            if bundle.get("trunk_bundle_version", 1) >= 2:
                require(out.get("chain_ok") is True, f"Exported ledger chain failed: {out}")
            else:
                require(out.get("chain_ok") is None and out.get("verdict") == "unverified",
                        f"Unhashed legacy bundle must report unverified chain: {out}")
            chain_label = "valid" if out.get("chain_ok") is True else "unverified"
            out = await call(session, "bundle_verify", {"bundle_json": bundle_json})
            require(out.get("all_valid") is True and len(out.get("claims", [])) == 1,
                    f"Exported bundle verification failed: {out}")
            print("MCP standing, generated claim, export and live replay: valid; "
                  f"chain: {chain_label}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="store_true", help="also check prepared test database")
    args = parser.parse_args()
    if args.database:
        require(bool(os.environ.get("CALX_DSN")), "--database requires explicit CALX_DSN")
    require(sys.prefix != sys.base_prefix, "Run this check from a fresh virtual environment")
    require(pq.__impl__ == "binary", f"[muse] did not provide binary libpq: {pq.__impl__}")
    require(tuple(map(int, version("mcp").split(".")[:2])) >= (1, 30), "MCP SDK too old")
    require(version("mcp").split(".", 1)[0] == "1", "Expected the MCP 1.x API")
    for name in ("calx", "nerode", "trunkit_mcp"):
        module = importlib.import_module(name)
        require(Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()),
                f"{name} imported from source, not the installed wheel: {module.__file__}")
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONPATH", "TRUNKIT_SRC")}
    env["TRUNKIT_ALLOW_WRITE"] = "0"
    with tempfile.TemporaryDirectory(prefix="trunkit-muse-smoke-") as cwd:
        for name in ("trunkit", "nerode"):
            subprocess.run([console_script(name), "--help"], check=True, cwd=cwd, env=env,
                           capture_output=True, timeout=30)
        asyncio.run(asyncio.wait_for(smoke(args.database, cwd, env), timeout=120))
    print("Installed [muse] wheel smoke passed")
    print("Dependency versions: " + ", ".join(
        f"{name}={version(name)}" for name in ("mcp", "pydantic", "pydantic-settings", "psycopg")
    ))


if __name__ == "__main__":
    main()
