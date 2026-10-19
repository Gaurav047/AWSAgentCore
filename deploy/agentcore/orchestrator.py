"""AgentCore Runtime entrypoint: LangGraph orchestrator (protocol HTTP, 0.0.0.0:8080)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from appointment_app.agentcore.runtime_app import main  # noqa: E402

if __name__ == "__main__":
    main()
