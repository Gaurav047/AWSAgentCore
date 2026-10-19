"""Conversation history backed by Amazon Bedrock AgentCore Memory (short-term events)."""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

log = logging.getLogger(__name__)


class AgentCoreMemoryStore:
    def __init__(self, memory_id: str, region_name: str, k_turns: int = 10, client=None):
        if client is None:
            from bedrock_agentcore.memory import MemoryClient

            client = MemoryClient(region_name=region_name)
        self.client = client
        self.memory_id = memory_id
        self.k_turns = k_turns

    def load(self, user_id: str, session_id: str) -> list[BaseMessage]:
        try:
            turns = self.client.get_last_k_turns(
                memory_id=self.memory_id, actor_id=user_id, session_id=session_id, k=self.k_turns
            )
        except Exception:  # memory is an enhancement; never fail the request over it
            log.exception("Failed to load AgentCore memory")
            return []
        messages: list[BaseMessage] = []
        for turn in turns:
            for msg in turn:
                content = msg.get("content")
                text = content.get("text", "") if isinstance(content, dict) else str(content or "")
                role = str(msg.get("role", "")).upper()
                if text and role == "USER":
                    messages.append(HumanMessage(text))
                elif text and role == "ASSISTANT":
                    messages.append(AIMessage(text))
        return messages

    def save(self, user_id: str, session_id: str, user_text: str, assistant_text: str) -> None:
        try:
            self.client.create_event(
                memory_id=self.memory_id,
                actor_id=user_id,
                session_id=session_id,
                messages=[(user_text, "USER"), (assistant_text, "ASSISTANT")],
            )
        except Exception:
            log.exception("Failed to save AgentCore memory event")


def create_memory(name: str, region_name: str) -> str:
    """Provision a short-term AgentCore Memory resource and return its id."""
    from bedrock_agentcore.memory import MemoryClient

    memory = MemoryClient(region_name=region_name).create_memory_and_wait(
        name=name,
        description="Conversation history for the appointment assistant",
        strategies=[],
        event_expiry_days=30,
    )
    return memory["id"]
