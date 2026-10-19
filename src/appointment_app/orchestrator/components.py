"""Production implementations of the orchestrator dependencies (Bedrock, A2A, MCP)."""

from __future__ import annotations

import logging

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableLambda

from ..a2a_client import A2AAgentClient
from ..agents.llm import langchain_model
from ..config import Settings, get_settings
from .graph import OrchestratorDeps, Route

log = logging.getLogger(__name__)

ROUTER_SYSTEM = """You route messages for a clinic's appointment assistant.
Classify the user's latest message, using the conversation for context
(e.g. "yes, book it" after a list of slots is a schedule request).

{format_instructions}
Return only the JSON object."""

LOOKUP_SYSTEM = """You answer read-only questions about the clinic's providers, availability and
existing appointments using the tools. Call get_current_time before interpreting relative dates.
Never claim to have booked, changed or cancelled anything; if the user wants that, tell them you
can do it and ask them to confirm. Be concise."""

CHAT_SYSTEM = """You are the friendly front desk of a clinic's appointment assistant. You can find
providers and open slots, book, reschedule or cancel appointments, and set email/SMS reminders.
Answer briefly and steer the user toward what you can help with."""

READ_ONLY_TOOLS = {
    "get_current_time",
    "list_providers",
    "find_available_slots",
    "next_available_slots",
    "get_appointment",
    "list_appointments",
    "list_reminders",
}

_KEYWORDS = [
    ("remind", ("remind", "reminder", "notify me", "sms", "text me")),
    ("schedule", ("book", "schedule", "reschedul", "cancel", "move my", "change my")),
    ("lookup", ("available", "availability", "open slot", "free slot", "who ", "which doctor", "my appointments", "when is")),
]


def keyword_route(text: str) -> Route:
    """Deterministic fallback when the LLM router's output can't be parsed."""
    lowered = text.lower()
    hits = {intent for intent, words in _KEYWORDS if any(w in lowered for w in words)}
    if "schedule" in hits:
        return Route(intent="schedule", also_remind="remind" in hits, reason="keyword fallback")
    for intent in ("remind", "lookup"):
        if intent in hits:
            return Route(intent=intent, reason="keyword fallback")
    return Route(intent="chat", reason="keyword fallback")


def make_llm_router(llm):
    parser = PydanticOutputParser(pydantic_object=Route)
    prompt = ChatPromptTemplate.from_messages(
        [("system", ROUTER_SYSTEM), MessagesPlaceholder("history"), ("human", "{input}")]
    ).partial(format_instructions=parser.get_format_instructions())
    chain = prompt | llm | RunnableLambda(lambda m: m.text) | parser

    async def route(text: str, history: list[BaseMessage]) -> Route:
        try:
            return await chain.ainvoke({"input": text, "history": history[-8:]})
        except OutputParserException:
            log.warning("Router output unparseable; using keyword fallback")
            return keyword_route(text)

    return route


def make_a2a_caller(settings: Settings):
    agents = {
        "scheduler": A2AAgentClient(settings.scheduler_a2a_url, bearer_token=settings.a2a_bearer_token),
        "reminder": A2AAgentClient(settings.reminder_a2a_url, bearer_token=settings.a2a_bearer_token),
    }

    async def call(agent: str, text: str, context_id: str) -> str:
        return await agents[agent].send_message(text, context_id=f"{agent}-{context_id}")

    return call


def make_mcp_lookup(llm, settings: Settings):
    """LangChain agent whose tools come from the MCP server via langchain-mcp-adapters."""
    from langchain.agents import create_agent
    from langchain_mcp_adapters.client import MultiServerMCPClient

    connection = {"url": settings.mcp_url, "transport": "streamable_http"}
    if settings.mcp_bearer_token:
        connection["headers"] = {"Authorization": f"Bearer {settings.mcp_bearer_token}"}
    client = MultiServerMCPClient({"appointments": connection})
    agent = None

    async def lookup(messages: list[BaseMessage]) -> str:
        nonlocal agent
        if agent is None:
            tools = [t for t in await client.get_tools() if t.name in READ_ONLY_TOOLS]
            agent = create_agent(llm, tools=tools, system_prompt=LOOKUP_SYSTEM)
        result = await agent.ainvoke({"messages": messages[-12:]})
        return result["messages"][-1].text

    return lookup


def make_chat(llm):
    async def chat(messages: list[BaseMessage]) -> str:
        reply = await llm.ainvoke([SystemMessage(CHAT_SYSTEM), *messages[-12:]])
        return reply.text

    return chat


def default_deps(settings: Settings | None = None) -> OrchestratorDeps:
    s = settings or get_settings()
    llm = langchain_model(s)
    return OrchestratorDeps(
        router=make_llm_router(llm),
        call_agent=make_a2a_caller(s),
        lookup=make_mcp_lookup(llm, s),
        chat=make_chat(llm),
    )
