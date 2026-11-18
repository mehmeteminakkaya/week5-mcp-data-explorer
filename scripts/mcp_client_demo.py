"""Drive the Data Explorer server over STDIO and print a reproducible transcript.

Usage:
    python scripts/mcp_client_demo.py            # happy path + bad input
    python scripts/mcp_client_demo.py --errors   # upstream failure mode

The client here is the same MCP stdio client a desktop client (Claude Desktop, an
MCP-aware IDE) uses under the hood, so the transcript is what an agent sees.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent.parent / "server.py"

# Windows consoles default to a legacy code page that cannot print "₺"; the transcript
# is UTF-8 everywhere else, so pin stdout too.
sys.stdout.reconfigure(encoding="utf-8")

# The dataset renamed "Turkey" to "Türkiye", so the realistic agent flow is
# search first, then ask for details with the exact name it just learned.
CALLS = [
    ("search_countries", {"query": "turk", "limit": 3}),
    ("search_countries", {"query": "   "}),
    ("search_countries", {"query": "atlantis"}),
    ("get_country_details", {"name": "Türkiye"}),
    ("get_country_details", {"name": "TUR"}),
    ("get_country_details", {"name": "nige"}),
    ("get_country_details", {"name": "Atlantis"}),
]

ERROR_CALLS = [
    ("search_countries", {"query": "nige"}),
    ("get_country_details", {"name": "Turkey"}),
]


def _payload(result):
    parts = []
    for item in result.content:
        text = getattr(item, "text", None)
        if text is None:
            parts.append(f"<{type(item).__name__}>")
            continue
        try:
            parts.append(json.loads(text))
        except json.JSONDecodeError:
            parts.append(text)
    return parts[0] if len(parts) == 1 else parts


def _show(value, indent="      "):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return "\n".join(indent + line for line in text.splitlines())


async def main() -> int:
    errors_only = "--errors" in sys.argv
    calls = ERROR_CALLS if errors_only else CALLS

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if errors_only:
        # Port 9 (discard) refuses connections: a genuine transport failure, no monkeypatching.
        env["DATA_EXPLORER_DATA_URL"] = "http://127.0.0.1:9/countries.json"
        print("### Error-mode session (DATA_EXPLORER_DATA_URL -> 127.0.0.1:9)\n")
    else:
        print("### Happy-path session\n")

    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print(f"server : {info.serverInfo.name} {info.serverInfo.version}")
            listed = await session.list_tools()
            for tool in listed.tools:
                params_ = ", ".join(
                    f"{name}: {spec.get('type', '?')}"
                    for name, spec in (tool.inputSchema.get("properties") or {}).items()
                )
                print(f"tool   : {tool.name}({params_})")
            print()

            for name, args in calls:
                result = await session.call_tool(name, args)
                print(f"> {name}({json.dumps(args, ensure_ascii=False)})")
                print(f"  isError={result.isError}")
                print(_show(_payload(result)))
                print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
