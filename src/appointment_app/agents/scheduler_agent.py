"""Scheduler agent (Strands): books, reschedules and cancels appointments using MCP tools.

Served as an A2A agent so the LangGraph orchestrator (or any A2A-capable client) can delegate to it.
"""

from __future__ import annotations

from strands import Agent

from .common import appointments_mcp_client, serve_a2a
from .llm import strands_model

NAME = "Appointment Scheduler"
DESCRIPTION = "Finds availability and books, reschedules or cancels appointments with providers."

SYSTEM_PROMPT = """You are the scheduling specialist for a multi-provider clinic.

How to work:
- Call get_current_time first whenever the request uses relative dates ("tomorrow", "next Tuesday").
- Find the provider with list_providers, then check find_available_slots or next_available_slots
  before proposing or booking a time. Never invent availability.
- To book you need the customer's name and email. If either is missing and not supplied in the
  conversation context, ask for it instead of guessing.
- If the requested slot is taken, offer the closest open alternatives the tools return.
- For reschedule or cancel requests without an appointment id, look it up with list_appointments
  using the customer's email. Confirm which appointment if several match.
- After any change, reply with a short confirmation: appointment id, provider, local date and time,
  and status. Keep replies concise and plain text."""

TOOLS = [
    "get_current_time",
    "list_providers",
    "find_available_slots",
    "next_available_slots",
    "book_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "get_appointment",
    "list_appointments",
]


def build_agent(tools: list, model=None) -> Agent:
    return Agent(
        name=NAME,
        description=DESCRIPTION,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        model=model or strands_model(),
        callback_handler=None,
    )


def serve(host: str = "0.0.0.0", port: int = 9001) -> None:
    serve_a2a(build_agent, appointments_mcp_client(TOOLS), host=host, port=port)


if __name__ == "__main__":
    serve()
