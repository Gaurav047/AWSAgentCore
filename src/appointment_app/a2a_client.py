"""Minimal A2A (Agent2Agent) client: agent-card discovery + JSON-RPC `message/send`.

Works against any A2A server (the Strands agents here, or third-party agents). For agents hosted
on AgentCore Runtime, pass the runtime invocation URL as `base_url` and an OAuth bearer token.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")


class A2AError(RuntimeError):
    pass


class A2AAgentClient:
    def __init__(
        self,
        base_url: str,
        *,
        bearer_token: str | None = None,
        timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        self._client_kwargs = {"headers": headers, "timeout": timeout, "transport": transport}
        self._card: dict[str, Any] | None = None

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(**self._client_kwargs)

    async def get_agent_card(self) -> dict[str, Any]:
        if self._card is None:
            async with self._http() as http:
                for path in CARD_PATHS:
                    resp = await http.get(self.base_url + path)
                    if resp.status_code == 200:
                        self._card = resp.json()
                        break
                else:
                    raise A2AError(f"No agent card found at {self.base_url}")
        return self._card

    async def send_message(self, text: str, *, context_id: str | None = None) -> str:
        message: dict[str, Any] = {
            "kind": "message",
            "role": "user",
            "messageId": uuid.uuid4().hex,
            "parts": [{"kind": "text", "text": text}],
        }
        if context_id:
            message["contextId"] = context_id
        payload = {
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/send",
            "params": {"message": message, "configuration": {"blocking": True}},
        }
        # POST to the configured base URL rather than card["url"]: servers bound to 0.0.0.0 or
        # behind a proxy often advertise an address the caller cannot reach.
        async with self._http() as http:
            resp = await http.post(self.base_url + "/", json=payload)
        if resp.status_code >= 400:
            raise A2AError(f"A2A HTTP {resp.status_code}: {resp.text[:500]}")
        body = resp.json()
        if "error" in body:
            raise A2AError(f"A2A error {body['error'].get('code')}: {body['error'].get('message')}")
        return extract_text(body.get("result", {}))


def _parts_text(parts: list[dict] | None) -> list[str]:
    return [p["text"] for p in parts or [] if p.get("kind", "text") == "text" and p.get("text")]


def extract_text(result: dict[str, Any]) -> str:
    """Pull the agent's reply out of a Message or Task result."""
    if result.get("kind") == "message" or "parts" in result:
        return "\n".join(_parts_text(result.get("parts")))
    texts: list[str] = []
    for artifact in result.get("artifacts") or []:
        texts.extend(_parts_text(artifact.get("parts")))
    if not texts:
        status_msg = (result.get("status") or {}).get("message") or {}
        texts = _parts_text(status_msg.get("parts"))
    if not texts:
        for msg in reversed(result.get("history") or []):
            if msg.get("role") == "agent":
                texts = _parts_text(msg.get("parts"))
                break
    state = (result.get("status") or {}).get("state")
    if state == "failed" and not texts:
        raise A2AError("Remote agent task failed")
    return "\n".join(texts)
