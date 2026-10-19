"""REST API: direct CRUD over the domain plus a /chat endpoint backed by the agent orchestrator."""

from __future__ import annotations

import uuid
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from ..domain import AppointmentService, NotFoundError, SlotUnavailableError, ValidationError, build_service

app = FastAPI(title="Appointment Manager", version="0.1.0")


@lru_cache
def get_service() -> AppointmentService:
    return build_service()


@lru_cache
def get_assistant():
    from ..orchestrator.assistant import build_assistant

    return build_assistant()


def _domain(fn, *args):
    try:
        return fn(*args)
    except NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except SlotUnavailableError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc


class BookRequest(BaseModel):
    provider_id: str
    customer_name: str
    customer_email: str
    start: str
    reason: str = ""
    duration_minutes: int | None = None


class RescheduleRequest(BaseModel):
    new_start: str


class CancelRequest(BaseModel):
    reason: str = ""


class ReminderRequest(BaseModel):
    minutes_before: int = 1440
    channel: str = "email"
    message: str = ""


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    customer_name: str | None = None
    customer_email: str | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/providers")
def providers(specialty: str | None = None, svc: AppointmentService = Depends(get_service)):
    return svc.list_providers(specialty)


@app.get("/providers/{provider_id}/slots")
def slots(provider_id: str, date: str, duration_minutes: int | None = None, svc: AppointmentService = Depends(get_service)):
    return _domain(svc.find_available_slots, provider_id, date, duration_minutes)


@app.get("/appointments")
def list_appointments(
    customer_email: str | None = None,
    provider_id: str | None = None,
    date: str | None = None,
    include_cancelled: bool = False,
    svc: AppointmentService = Depends(get_service),
):
    return _domain(svc.list, customer_email, provider_id, date, include_cancelled)


@app.post("/appointments", status_code=201)
def book(req: BookRequest, svc: AppointmentService = Depends(get_service)):
    return _domain(
        svc.book, req.provider_id, req.customer_name, req.customer_email, req.start, req.reason, req.duration_minutes
    )


@app.get("/appointments/{appointment_id}")
def get_appointment(appointment_id: str, svc: AppointmentService = Depends(get_service)):
    return _domain(svc.get, appointment_id)


@app.post("/appointments/{appointment_id}/reschedule")
def reschedule(appointment_id: str, req: RescheduleRequest, svc: AppointmentService = Depends(get_service)):
    return _domain(svc.reschedule, appointment_id, req.new_start)


@app.post("/appointments/{appointment_id}/cancel")
def cancel(appointment_id: str, req: CancelRequest, svc: AppointmentService = Depends(get_service)):
    return _domain(svc.cancel, appointment_id, req.reason)


@app.post("/appointments/{appointment_id}/reminders", status_code=201)
def add_reminder(appointment_id: str, req: ReminderRequest, svc: AppointmentService = Depends(get_service)):
    return _domain(svc.schedule_reminder, appointment_id, req.minutes_before, req.channel, req.message)


@app.post("/chat")
async def chat(req: ChatRequest, assistant=Depends(get_assistant)):
    session_id = req.session_id or uuid.uuid4().hex
    customer = {"name": req.customer_name, "email": req.customer_email}
    result = await assistant.chat(
        req.message, session_id=session_id, user_id=req.customer_email or "anonymous", customer=customer
    )
    return {"session_id": session_id, "reply": result.reply, "intent": result.intent, "agents": result.agents}
