"""Call the vroid-files MCP server over stdio from the shell, the same way an MCP client does.

  python client.py list
  python client.py <tool> '<json arguments>'
  python client.py <tool> @args.json

Run it with the Python that has the requirements installed (for example the venv's python).
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
PY = sys.executable
SERVER = str(HERE / "server.py")


async def run(tool, args):
    async with stdio_client(StdioServerParameters(command=PY, args=[SERVER])) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            if tool == "list":
                for t in (await s.list_tools()).tools:
                    print(f"{t.name}: {(t.description or '').splitlines()[0]}")
                return 0
            res = await s.call_tool(tool, args)
            err = getattr(res, "is_error", False) or getattr(res, "isError", False)
            structured = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
            if structured is not None and not err:
                # list results are wrapped as {"result": [...]} by the SDK
                if isinstance(structured, dict) and set(structured) == {"result"}:
                    structured = structured["result"]
                print(json.dumps(structured, indent=1))
            else:
                texts = [getattr(c, "text", str(c)) for c in res.content or []]
                print(texts[0] if len(texts) == 1 else "[" + ",".join(texts) + "]")
            return 1 if err else 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    tool = sys.argv[1]
    raw = sys.argv[2] if len(sys.argv) > 2 else "{}"
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text(encoding="utf-8")
    return asyncio.run(run(tool, json.loads(raw)))


if __name__ == "__main__":
    sys.exit(main())
