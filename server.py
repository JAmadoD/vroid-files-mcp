"""Run the vroid-files MCP server from a checkout: python server.py

Same as running vroid-files-mcp or python -m vroid_files_mcp after installing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vroid_files_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
