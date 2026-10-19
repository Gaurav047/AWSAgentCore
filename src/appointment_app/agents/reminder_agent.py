"""Reminder agent (Strands): plans and schedules appointment reminders.

Combines remote MCP tools (schedule_reminder, list_reminders, ...) with a local Strands @tool
that encodes the clinic's reminder policy. Served over A2A.
"""

from __future__ import annotations

from datetime import datetime

from strands import Agent, tool

from .common import appointments_mcp_client, serve_a2a
from .llm import strands_model

NAME = "Appointment Reminders"
DESCRIPTION = "Schedules and reviews email/SMS reminders for booked appointments."

SYSTEM_PROMPT = """You manage reminders for clinic appointments.

- Use get_appointment (or list_appointments by customer email) to find the appointment.
- Unless the user specifies timing, call reminder_policy to get the recommended offsets and
  schedule one reminder per offset with schedule_reminder.
- Use the channel the user asked for (email or sms); default to email.
- Reply with a short summary of the reminders now scheduled (time and channel)."""

TOOLS = ["get_current_time", "get_appointment", "list_appointments", "schedule_reminder", "list_reminders"]


@tool
def reminder_policy(appointment_start: str, now: str) -> dict:
    """Recommended reminder offsets (minutes before the appointment) for an appointment.

    Args:
        appointment_start: ISO-8601 start of the appointment.
        now: ISO-8601 current time (from get_current_time).
    """
    lead = (datetime.fromisoformat(appointment_start) - datetime.fromisoformat(now)).total_seconds() / 60
    candidates = [(7 * 24 * 60, "one week before"), (24 * 60, "one day before"), (120, "two hours before")]
    offsets = [{"minutes_before": m, "label": label} for m, label in candidates if m < lead - 30]
    return {"lead_time_minutes": int(lead), "recommended": offsets or [{"minutes_before": max(int(lead) // 2, 5), "label": "halfway to the appointment"}]}


def build_agent(tools: list, model=None) -> Agent:
    return Agent(
        name=NAME,
        description=DESCRIPTION,
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        model=model or strands_model(),
        callback_handler=None,
    )


def serve(host: str = "0.0.0.0", port: int = 9002) -> None:
    serve_a2a(build_agent, appointments_mcp_client(TOOLS), extra_tools=[reminder_policy], host=host, port=port)


if __name__ == "__main__":
    serve()
