"""LangGraph orchestrator with injected fakes - no Bedrock or network."""

import pytest

from appointment_app.orchestrator import Assistant, OrchestratorDeps, build_graph
from appointment_app.orchestrator.components import keyword_route


class Fakes:
    def __init__(self, scheduler_reply="Booked apt_0123456789 with Dr. Asha Rao on Tue 20 Oct 10:00."):
        self.calls = []
        self.scheduler_reply = scheduler_reply

    async def router(self, text, history):
        return keyword_route(text)

    async def call_agent(self, agent, text, context_id):
        self.calls.append((agent, text, context_id))
        return self.scheduler_reply if agent == "scheduler" else "Reminder set for 19 Oct 10:00 by email."

    async def lookup(self, messages):
        self.calls.append(("lookup", messages[-1].text, None))
        return "Dr. Rao has 3 openings tomorrow."

    async def chat(self, messages):
        return "Hello! I can help you book appointments."

    def deps(self):
        return OrchestratorDeps(self.router, self.call_agent, self.lookup, self.chat)


@pytest.fixture
def fakes():
    return Fakes()


async def test_booking_with_reminder_chains_both_agents(fakes):
    assistant = Assistant(build_graph(fakes.deps()))
    result = await assistant.chat(
        "Book Dr Rao tomorrow at 10 and remind me the day before",
        session_id="s1",
        customer={"name": "Jane", "email": "jane@example.com"},
    )
    assert result.intent == "schedule"
    assert result.agents == ["scheduler", "reminder"]
    assert "apt_0123456789" in result.reply and "Reminder set" in result.reply
    scheduler_req, reminder_req = fakes.calls[0][1], fakes.calls[1][1]
    assert "email=jane@example.com" in scheduler_req
    assert "apt_0123456789" in reminder_req  # scheduler output is handed to the reminder agent
    assert fakes.calls[0][2] == "s1"


async def test_reminder_skipped_when_booking_failed():
    fakes = Fakes(scheduler_reply="What is your email address?")
    result = await Assistant(build_graph(fakes.deps())).chat("Book Dr Rao at 10 and remind me", session_id="s")
    assert result.agents == ["scheduler"]


@pytest.mark.parametrize(
    "text,intent,agent",
    [
        ("Which doctor has availability on Friday?", "lookup", "lookup"),
        ("Set an SMS reminder for apt_0123456789", "remind", "reminder"),
        ("hi there", "chat", "chat"),
        ("Please cancel my appointment", "schedule", "scheduler"),
    ],
)
async def test_routing(fakes, text, intent, agent):
    result = await Assistant(build_graph(fakes.deps())).chat(text, session_id="s")
    assert (result.intent, result.agents) == (intent, [agent])


async def test_history_is_carried_between_turns(fakes):
    seen = []

    async def router(text, history):
        seen.append(len(history))
        return keyword_route(text)

    fakes.router = router
    assistant = Assistant(build_graph(fakes.deps()))
    await assistant.chat("hi", session_id="s")
    await assistant.chat("hello again", session_id="s")
    await assistant.chat("hi", session_id="other")
    assert seen == [0, 2, 0]
