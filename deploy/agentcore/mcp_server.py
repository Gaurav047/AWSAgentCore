"""AgentCore Runtime entrypoint: MCP server (protocol MCP, 0.0.0.0:8000/mcp)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from appointment_app.mcp_server.server import main  # noqa: E402

if __name__ == "__main__":
    main()
