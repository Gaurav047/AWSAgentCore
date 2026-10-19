"""LangGraph supervisor that routes each user turn to the right specialist.

    START -> route -> scheduler (A2A) -> [reminder (A2A)] -> finalize -> END
                   -> reminder  (A2A) ----------------------> finalize
                   -> lookup    (LangChain agent over MCP) -> finalize
                   -> chat      (plain LLM reply) ----------> finalize

Every external dependency is injected through OrchestratorDeps, so the graph can be tested
without Bedrock or network access.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

Intent = Literal["schedule", "remind", "lookup", "chat"]
_APPOINTMENT_ID = re.compile(r"\bapt_[0-9a-f]{10}\b")


class Route(BaseModel):
    intent: Intent = Field(
        description=(
            "schedule: book/reschedule/cancel or check availability in order to book; "
            "remind: create or review reminders; "
            "lookup: read-only questions about providers, appointments or availability; "
            "chat: greetings or anything unrelated to appointments"
        )
    )
    also_remind: bool = Field(False, description="True when the user also asked for a reminder while booking")
    reason: str = Field("", description="One short sentence explaining the choice")


class OrchestratorState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    session_id: str
    customer: dict
    route: dict
    outputs: Annotated[list[dict], operator.add]


@dataclass
class OrchestratorDeps:
    router: Callable[[str, list[BaseMessage]], Awaitable[Route]]
    call_agent: Callable[[str, str, str], Awaitable[str]]  # (agent_name, text, context_id) -> reply
    lookup: Callable[[list[BaseMessage]], Awaitable[str]]
    chat: Callable[[list[BaseMessage]], Awaitable[str]]


def _last_user_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return msg.text if isinstance(msg.text, str) else str(msg.content)
    return ""


def _customer_line(customer: dict | None) -> str:
    if not customer:
        return ""
    known = {k: v for k, v in customer.items() if v}
    return "Customer on file: " + ", ".join(f"{k}={v}" for k, v in known.items()) + "\n" if known else ""


def build_graph(deps: OrchestratorDeps):
    """Compile the graph. Conversation history is owned by the caller (see Assistant), so each
    invocation starts from fresh state and `outputs` only holds this turn's specialist replies."""

    async def route(state: OrchestratorState) -> dict:
        messages = state["messages"]
        decision = await deps.router(_last_user_text(messages), messages[:-1])
        return {"route": decision.model_dump()}

    async def scheduler(state: OrchestratorState) -> dict:
        request = _customer_line(state.get("customer")) + _last_user_text(state["messages"])
        reply = await deps.call_agent("scheduler", request, state.get("session_id", "default"))
        return {"outputs": [{"agent": "scheduler", "text": reply}]}

    async def reminder(state: OrchestratorState) -> dict:
        request = _customer_line(state.get("customer")) + _last_user_text(state["messages"])
        booked = [o["text"] for o in state.get("outputs", []) if o["agent"] == "scheduler"]
        if booked:
            request += f"\n\nThe scheduler just reported:\n{booked[-1]}\nSchedule reminders for that appointment."
        reply = await deps.call_agent("reminder", request, state.get("session_id", "default"))
        return {"outputs": [{"agent": "reminder", "text": reply}]}

    async def lookup(state: OrchestratorState) -> dict:
        return {"outputs": [{"agent": "lookup", "text": await deps.lookup(state["messages"])}]}

    async def chat(state: OrchestratorState) -> dict:
        return {"outputs": [{"agent": "chat", "text": await deps.chat(state["messages"])}]}

    def finalize(state: OrchestratorState) -> dict:
        texts = [o["text"].strip() for o in state.get("outputs", []) if o["text"].strip()]
        return {"messages": [AIMessage("\n\n".join(texts) or "Sorry, I couldn't complete that request.")]}

    def pick_specialist(state: OrchestratorState) -> str:
        return {"schedule": "scheduler", "remind": "reminder", "lookup": "lookup"}.get(state["route"]["intent"], "chat")

    def after_scheduler(state: OrchestratorState) -> str:
        # Chain into the reminder agent only if a booking actually produced an appointment id.
        last = state["outputs"][-1]["text"]
        return "reminder" if state["route"].get("also_remind") and _APPOINTMENT_ID.search(last) else "finalize"

    graph = StateGraph(OrchestratorState)
    for name, fn in [
        ("route", route),
        ("scheduler", scheduler),
        ("reminder", reminder),
        ("lookup", lookup),
        ("chat", chat),
        ("finalize", finalize),
    ]:
        graph.add_node(name, fn)
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", pick_specialist, ["scheduler", "reminder", "lookup", "chat"])
    graph.add_conditional_edges("scheduler", after_scheduler, ["reminder", "finalize"])
    for name in ("reminder", "lookup", "chat"):
        graph.add_edge(name, "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
