"""Starts the job search MCP server from any working directory (MCP clients such as Hermes Agent in WSL or
Claude Desktop launch servers from their own folder, where `python -m jobsearch` can't be found).

    <project>/.venv/Scripts/python.exe <project>/scripts/jobsearch_mcp.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobsearch.__main__ import setup_logging  # noqa: E402
from jobsearch.mcp_server import serve  # noqa: E402

setup_logging()
serve()
