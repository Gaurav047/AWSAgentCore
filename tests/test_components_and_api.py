from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from appointment_app.api import app as api_module
from appointment_app.orchestrator.components import make_llm_router


async def test_llm_router_parses_fenced_json_and_falls_back():
    llm = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage('```json\n{"intent": "schedule", "also_remind": true, "reason": "booking"}\n```'),
                AIMessage("I think this is about reminders"),
            ]
        )
    )
    route = make_llm_router(llm)
    first = await route("book me in and remind me", [])
    assert (first.intent, first.also_remind) == ("schedule", True)
    fallback = await route("please send me a reminder", [])
    assert fallback.intent == "remind" and fallback.reason == "keyword fallback"


def test_rest_api(service):
    api_module.app.dependency_overrides[api_module.get_service] = lambda: service
    client = TestClient(api_module.app)
    try:
        assert client.get("/providers", params={"specialty": "derm"}).json()[0]["id"] == "dr-fischer"
        slot = client.get("/providers/dr-rao/slots", params={"date": "2026-10-20"}).json()[0]
        body = {"provider_id": "dr-rao", "customer_name": "Jane", "customer_email": "jane@example.com", "start": slot["start"]}
        created = client.post("/appointments", json=body)
        assert created.status_code == 201
        assert client.post("/appointments", json=body).status_code == 409
        appt_id = created.json()["id"]
        assert client.post(f"/appointments/{appt_id}/reminders", json={"minutes_before": 60}).status_code == 201
        assert client.post(f"/appointments/{appt_id}/cancel", json={}).json()["status"] == "cancelled"
        assert client.get("/appointments/apt_missing").status_code == 404
        assert client.get("/providers/dr-rao/slots", params={"date": "bad"}).status_code == 422
    finally:
        api_module.app.dependency_overrides.clear()
