import json

import httpx
import pytest

from appointment_app.a2a_client import A2AAgentClient, A2AError, extract_text


def _transport(result=None, error=None, card_path="/.well-known/agent-card.json"):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            if request.url.path == card_path:
                return httpx.Response(200, json={"name": "Appointment Scheduler", "url": "http://0.0.0.0:9001/"})
            return httpx.Response(404)
        seen["body"] = json.loads(request.content)
        body = {"jsonrpc": "2.0", "id": seen["body"]["id"]}
        body.update({"error": error} if error else {"result": result})
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler), seen


async def test_card_discovery_falls_back_to_legacy_path():
    transport, _ = _transport(card_path="/.well-known/agent.json")
    card = await A2AAgentClient("http://agent", transport=transport).get_agent_card()
    assert card["name"] == "Appointment Scheduler"


async def test_send_message_parses_task_artifacts_and_sends_context():
    task = {
        "kind": "task",
        "id": "t1",
        "contextId": "ctx",
        "status": {"state": "completed"},
        "artifacts": [{"artifactId": "a", "parts": [{"kind": "text", "text": "Booked apt_0123456789"}]}],
    }
    transport, seen = _transport(result=task)
    reply = await A2AAgentClient("http://agent", transport=transport).send_message("book", context_id="ctx")
    assert reply == "Booked apt_0123456789"
    msg = seen["body"]["params"]["message"]
    assert seen["body"]["method"] == "message/send"
    assert msg["contextId"] == "ctx" and msg["parts"][0]["text"] == "book"


async def test_send_message_raises_on_jsonrpc_error():
    transport, _ = _transport(error={"code": -32603, "message": "boom"})
    with pytest.raises(A2AError, match="boom"):
        await A2AAgentClient("http://agent", transport=transport).send_message("hi")


def test_extract_text_from_message_and_status():
    assert extract_text({"kind": "message", "parts": [{"kind": "text", "text": "hello"}]}) == "hello"
    status_only = {"kind": "task", "status": {"state": "completed", "message": {"parts": [{"kind": "text", "text": "done"}]}}}
    assert extract_text(status_only) == "done"
