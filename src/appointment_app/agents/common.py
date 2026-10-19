"""Shared plumbing for Strands agents that get their tools over MCP and are served over A2A."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from strands import Agent
from strands.multiagent.a2a import A2AServer
from strands.tools.mcp import MCPClient

from ..config import Settings, get_settings

log = logging.getLogger(__name__)


def appointments_mcp_client(allowed_tools: list[str] | None = None, settings: Settings | None = None) -> MCPClient:
    s = settings or get_settings()
    headers = {"Authorization": f"Bearer {s.mcp_bearer_token}"} if s.mcp_bearer_token else None
    return MCPClient(
        url=s.mcp_url,
        headers=headers,
        tool_filters={"allowed": allowed_tools} if allowed_tools else None,
    )


def serve_a2a(
    build_agent: Callable[[list], Agent],
    mcp_client: MCPClient,
    extra_tools: list | None = None,
    host: str = "0.0.0.0",
    port: int = 9000,
) -> None:
    """Open the MCP session, then expose the agent over A2A.

    A fresh agent is created per A2A `contextId`, so concurrent conversations never share
    Strands conversation state (a single Agent instance is not safe for concurrent use).

    On AgentCore Runtime (`--protocol A2A`) the container must listen on 0.0.0.0:9000 at the
    root path; AGENTCORE_RUNTIME_URL (or A2A_PUBLIC_URL) is advertised in the agent card.
    """
    with mcp_client:
        tools = mcp_client.list_tools_sync() + list(extra_tools or [])
        log.info("Loaded %d tools: %s", len(tools), [getattr(t, "tool_name", t) for t in tools])
        server = A2AServer(
            agent_factory=lambda _context_id: build_agent(tools),
            host=host,
            port=port,
            http_url=os.environ.get("AGENTCORE_RUNTIME_URL") or os.environ.get("A2A_PUBLIC_URL"),
            serve_at_root=True,
        )
        server.serve()
