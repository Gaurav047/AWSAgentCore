"""Strands agents: construction, A2A agent card, local tools, and the AgentCore entrypoint."""

import os

import pytest

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

from strands.multiagent.a2a import A2AServer  # noqa: E402

from appointment_app.agents import reminder_agent, scheduler_agent  # noqa: E402


@pytest.mark.parametrize("module", [scheduler_agent, reminder_agent])
def test_agent_card(module):
    server = A2AServer(agent_factory=lambda _ctx: module.build_agent([reminder_agent.reminder_policy]), port=9100)
    card = server.public_agent_card
    assert card.name == module.NAME
    assert card.description == module.DESCRIPTION


def test_reminder_policy():
    out = reminder_agent.reminder_policy(appointment_start="2026-10-29T10:00:00+00:00", now="2026-10-19T08:00:00+00:00")
    assert [o["minutes_before"] for o in out["recommended"]] == [10080, 1440, 120]
    soon = reminder_agent.reminder_policy(appointment_start="2026-10-19T09:00:00+00:00", now="2026-10-19T08:00:00+00:00")
    assert soon["recommended"][0]["minutes_before"] == 30


async def test_agentcore_entrypoint(monkeypatch):
    from appointment_app.agentcore import runtime_app
    from appointment_app.orchestrator import Assistant, build_graph
    from tests.test_orchestrator import Fakes

    monkeypatch.setattr(runtime_app, "_assistant", Assistant(build_graph(Fakes().deps())))

    class Ctx:
        session_id = "runtime-session"

    out = await runtime_app.invoke({"prompt": "hello"}, Ctx())
    assert out["session_id"] == "runtime-session" and out["intent"] == "chat"
    assert (await runtime_app.invoke({}, Ctx()))["error"]
