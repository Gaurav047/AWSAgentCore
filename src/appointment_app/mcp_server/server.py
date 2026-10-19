"""MCP server: the single tool surface over the appointment domain.

Runs as streamable-HTTP on 0.0.0.0:8000/mcp in stateless mode, which is exactly the contract
AgentCore Runtime expects for `--protocol MCP` deployments. Both the Strands agents and the
LangChain/LangGraph lookup agent consume these tools over MCP.
"""

from __future__ import annotations

import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from ..domain import AppointmentError, AppointmentService, build_service

mcp = FastMCP(
    "appointments",
    instructions=(
        "Tools for managing appointments with providers (doctors, dentists, etc). "
        "Call get_current_time before interpreting relative dates like 'tomorrow'. "
        "Always check find_available_slots before booking."
    ),
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8000")),
    stateless_http=True,
)

_service: AppointmentService | None = None


def get_service() -> AppointmentService:
    global _service
    if _service is None:
        _service = build_service()
    return _service


def set_service(service: AppointmentService) -> None:
    """Swap the backing service (used by tests and embedded deployments)."""
    global _service
    _service = service


def _call(fn, *args, **kwargs) -> Any:
    try:
        result = fn(*args, **kwargs)
    except AppointmentError as exc:
        raise ToolError(str(exc)) from exc
    if isinstance(result, list):
        return [r.model_dump(mode="json") for r in result]
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------- tools


@mcp.tool()
def get_current_time() -> dict:
    """Current date/time in the clinic's timezone. Use it to resolve 'today', 'tomorrow', 'next Monday'."""
    now = get_service().now()
    return {"now": now.isoformat(), "weekday": now.strftime("%A"), "timezone": get_service().tz.key}


@mcp.tool()
def list_providers(specialty: str | None = None) -> list[dict]:
    """List bookable providers, optionally filtered by specialty or name (e.g. 'dentistry')."""
    return _call(get_service().list_providers, specialty)


@mcp.tool()
def find_available_slots(provider_id: str, date: str, duration_minutes: int | None = None) -> list[dict]:
    """Free time slots for a provider on a date (YYYY-MM-DD). Duration defaults to the provider's slot length."""
    return _call(get_service().find_available_slots, provider_id, date, duration_minutes)


@mcp.tool()
def next_available_slots(provider_id: str, limit: int = 5) -> list[dict]:
    """The earliest open slots for a provider over the next two weeks."""
    return _call(get_service().next_available_slots, provider_id, 14, limit)


@mcp.tool()
def book_appointment(
    provider_id: str,
    customer_name: str,
    customer_email: str,
    start: str,
    reason: str = "",
    duration_minutes: int | None = None,
) -> dict:
    """Book an appointment. `start` is ISO-8601 (e.g. 2026-10-21T14:30); naive times use the clinic timezone."""
    return _call(get_service().book, provider_id, customer_name, customer_email, start, reason, duration_minutes)


@mcp.tool()
def cancel_appointment(appointment_id: str, reason: str = "") -> dict:
    """Cancel a booked appointment. Pending reminders for it are cancelled too."""
    return _call(get_service().cancel, appointment_id, reason)


@mcp.tool()
def reschedule_appointment(appointment_id: str, new_start: str) -> dict:
    """Move a booked appointment to a new ISO-8601 start time, keeping its duration."""
    return _call(get_service().reschedule, appointment_id, new_start)


@mcp.tool()
def get_appointment(appointment_id: str) -> dict:
    """Fetch one appointment by id."""
    return _call(get_service().get, appointment_id)


@mcp.tool()
def list_appointments(
    customer_email: str | None = None,
    provider_id: str | None = None,
    date: str | None = None,
    include_cancelled: bool = False,
    upcoming_only: bool = False,
) -> list[dict]:
    """Search appointments by customer email, provider and/or date (YYYY-MM-DD)."""
    return _call(get_service().list, customer_email, provider_id, date, include_cancelled, upcoming_only)


@mcp.tool()
def schedule_reminder(appointment_id: str, minutes_before: int = 1440, channel: str = "email", message: str = "") -> dict:
    """Schedule a reminder `minutes_before` the appointment via 'email' or 'sms'. Empty message uses a default template."""
    return _call(get_service().schedule_reminder, appointment_id, minutes_before, channel, message)


@mcp.tool()
def list_reminders(appointment_id: str | None = None, customer_email: str | None = None) -> list[dict]:
    """List reminders for an appointment or customer."""
    return _call(get_service().list_reminders, appointment_id, customer_email)


# ---------------------------------------------------------------------------- resources & prompts


@mcp.resource("appointments://providers", mime_type="application/json")
def providers_resource() -> list[dict]:
    """Directory of all providers and their working hours."""
    return [p.model_dump(mode="json") for p in get_service().list_providers()]


@mcp.prompt()
def book_visit(specialty: str, preferred_day: str = "") -> str:
    """Prompt template that walks an assistant through booking a visit."""
    when = f" on {preferred_day}" if preferred_day else ""
    return (
        f"Help me book a {specialty} appointment{when}. First find a matching provider, "
        "show me up to three open slots, and ask for my name and email before booking."
    )


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
