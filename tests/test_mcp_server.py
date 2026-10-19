"""Exercise the MCP server over a real MCP client session (in-memory transport)."""

import json

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from appointment_app.mcp_server import mcp, set_service


@pytest.fixture(autouse=True)
def _wire(service):
    set_service(service)


def _json(result):
    return json.loads(result.content[0].text)


async def test_tools_resources_prompts_listed():
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert {"book_appointment", "find_available_slots", "schedule_reminder", "get_current_time"} <= tools
        resources = (await client.list_resources()).resources
        assert str(resources[0].uri) == "appointments://providers"
        prompts = (await client.list_prompts()).prompts
        assert prompts[0].name == "book_visit"


async def test_book_flow_over_mcp():
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        slots = await client.call_tool("find_available_slots", {"provider_id": "dr-santos", "date": "2026-10-20"})
        first = json.loads(slots.content[0].text)
        booked = _json(
            await client.call_tool(
                "book_appointment",
                {
                    "provider_id": "dr-santos",
                    "customer_name": "Jane Doe",
                    "customer_email": "jane@example.com",
                    "start": first["start"],
                },
            )
        )
        assert booked["status"] == "booked" and booked["id"].startswith("apt_")

        clash = await client.call_tool(
            "book_appointment",
            {"provider_id": "dr-santos", "customer_name": "Bob", "customer_email": "bob@example.com", "start": first["start"]},
        )
        assert clash.isError and "overlaps" in clash.content[0].text

        reminder = _json(await client.call_tool("schedule_reminder", {"appointment_id": booked["id"], "minutes_before": 120}))
        assert reminder["status"] == "pending"
