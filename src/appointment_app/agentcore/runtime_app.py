"""Amazon Bedrock AgentCore Runtime entrypoint for the orchestrator (HTTP protocol, port 8080).

Invoke with a JSON payload:
    {"prompt": "Book a dentist tomorrow at 10", "user_id": "jane@example.com",
     "customer": {"name": "Jane Doe", "email": "jane@example.com"}}

The runtime session id (X-Amzn-Bedrock-AgentCore-Runtime-Session-Id) keys the conversation;
history is kept in AgentCore Memory when AGENTCORE_MEMORY_ID is set, else in process memory.
"""

from __future__ import annotations

import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from ..config import get_settings
from ..orchestrator.assistant import Assistant, InMemoryConversationStore, build_assistant

logging.basicConfig(level=logging.INFO)
app = BedrockAgentCoreApp()
_assistant: Assistant | None = None


def get_assistant() -> Assistant:
    global _assistant
    if _assistant is None:
        settings = get_settings()
        if settings.agentcore_memory_id:
            from .memory import AgentCoreMemoryStore

            store = AgentCoreMemoryStore(settings.agentcore_memory_id, settings.aws_region)
        else:
            store = InMemoryConversationStore()
        _assistant = build_assistant(store)
    return _assistant


@app.entrypoint
async def invoke(payload: dict, context) -> dict:
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return {"error": "payload.prompt is required"}
    session_id = getattr(context, "session_id", None) or payload.get("session_id") or "default"
    customer = payload.get("customer") or {}
    user_id = payload.get("user_id") or customer.get("email") or "anonymous"
    result = await get_assistant().chat(prompt, session_id=session_id, user_id=user_id, customer=customer)
    return {"response": result.reply, "intent": result.intent, "agents": result.agents, "session_id": session_id}


def main() -> None:
    app.run()


if __name__ == "__main__":
    main()
