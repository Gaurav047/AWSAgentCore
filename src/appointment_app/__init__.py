"""Agentic appointment management application."""

from .config import load_env

# Runs before any submodule reads os.environ (e.g. the MCP server's host/port at import time).
load_env()

__version__ = "0.1.0"
