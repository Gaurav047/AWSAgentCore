"""Session-aware front door: loads history, runs the graph, persists the turn."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


class ConversationStore(Protocol):
    def load(self, user_id: str, session_id: str) -> list[BaseMessage]: ...

    def save(self, user_id: str, session_id: str, user_text: str, assistant_text: str) -> None: ...


class InMemoryConversationStore:
    def __init__(self, max_turns: int = 20):
        self.max_turns = max_turns
        self._turns: dict[tuple[str, str], list[BaseMessage]] = defaultdict(list)

    def load(self, user_id: str, session_id: str) -> list[BaseMessage]:
        return list(self._turns[(user_id, session_id)])

    def save(self, user_id: str, session_id: str, user_text: str, assistant_text: str) -> None:
        history = self._turns[(user_id, session_id)]
        history.extend([HumanMessage(user_text), AIMessage(assistant_text)])
        del history[: max(0, len(history) - 2 * self.max_turns)]


@dataclass
class ChatResult:
    reply: str
    intent: str
    agents: list[str] = field(default_factory=list)


class Assistant:
    def __init__(self, graph, store: ConversationStore | None = None):
        self.graph = graph
        self.store = store or InMemoryConversationStore()

    async def chat(
        self,
        text: str,
        *,
        session_id: str,
        user_id: str = "anonymous",
        customer: dict | None = None,
    ) -> ChatResult:
        history = self.store.load(user_id, session_id)
        state = await self.graph.ainvoke(
            {
                "messages": [*history, HumanMessage(text)],
                "session_id": session_id,
                "customer": customer or {},
            }
        )
        reply = state["messages"][-1].text
        self.store.save(user_id, session_id, text, reply)
        return ChatResult(
            reply=reply,
            intent=state["route"]["intent"],
            agents=[o["agent"] for o in state.get("outputs", [])],
        )


def build_assistant(store: ConversationStore | None = None) -> Assistant:
    from .components import default_deps
    from .graph import build_graph

    return Assistant(build_graph(default_deps()), store)
