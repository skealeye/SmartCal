"""Entry point of SmartCal's MCP server (built as SmartCal-mcp.exe; see README)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smartcal.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
