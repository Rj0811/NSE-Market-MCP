#!/usr/bin/env python3
"""Call NSE's official remote MCP servers and save JSON results.

This is a client for NSE's hosted MCP endpoints, not a server implementation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SERVERS = {
    "bhavcopy": "https://mcp.nseindia.in/bhavcopy/cm/mcp",
    "cmmkt": "https://mcp.nseindia.in/cmmkt/mcp",
}


def json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


async def run_with_session(server: str, operation):
    url = SERVERS[server]
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await operation(session)


async def discover(server: str) -> dict[str, Any]:
    async def operation(session):
        result = await session.list_tools()
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.inputSchema,
            }
            for tool in result.tools
        ]
        return {
            "server": server,
            "url": SERVERS[server],
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "tools_count": len(tools),
            "tools": tools,
        }

    return await run_with_session(server, operation)


async def call_tool(server: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async def operation(session):
        available = await session.list_tools()
        names = {tool.name for tool in available.tools}
        if tool_name not in names:
            raise ValueError(
                f"Tool '{tool_name}' not found on {server}. "
                f"Run discover mode to see the current tool names."
            )
        result = await session.call_tool(tool_name, arguments)
        return {
            "server": server,
            "tool": tool_name,
            "arguments": arguments,
            "is_error": bool(getattr(result, "isError", False)),
            "content": [json_safe(item) for item in result.content],
            "structuredContent": json_safe(getattr(result, "structuredContent", None)),
        }

    return await run_with_session(server, operation)


def save_json(data: Any, path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {target}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="NSE hosted MCP client")
    parser.add_argument("--mode", choices=("discover", "call"), default="discover")
    parser.add_argument("--server", choices=("bhavcopy", "cmmkt", "both"), default="both")
    parser.add_argument("--tool", help="Exact NSE MCP tool name (call mode)")
    parser.add_argument("--args", default="{}", help="Tool arguments as a JSON object")
    parser.add_argument("--output", default="output/nse-result.json")
    opts = parser.parse_args()

    servers = list(SERVERS) if opts.server == "both" else [opts.server]
    try:
        if opts.mode == "discover":
            results = []
            for server in servers:
                print(f"Discovering {server}...")
                results.append(await discover(server))
            save_json({"servers": results}, opts.output)
            for item in results:
                print(f"{item['server']}: {item['tools_count']} tools")
        else:
            if len(servers) != 1:
                raise ValueError("Call mode requires --server bhavcopy or --server cmmkt")
            if not opts.tool:
                raise ValueError("--tool is required in call mode")
            arguments = json.loads(opts.args)
            if not isinstance(arguments, dict):
                raise ValueError("--args must decode to a JSON object")
            result = await call_tool(servers[0], opts.tool, arguments)
            save_json(result, opts.output)
            if result["is_error"]:
                print("The NSE MCP tool returned an error.", file=sys.stderr)
                return 2
        return 0
    except Exception as exc:
        print(f"NSE MCP error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
