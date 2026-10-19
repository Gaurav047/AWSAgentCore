from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class AppointmentStatus(StrEnum):
    BOOKED = "booked"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class ReminderStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    CANCELLED = "cancelled"


class Provider(BaseModel):
    """A person or resource that can be booked (doctor, stylist, meeting room...)."""

    id: str
    name: str
    specialty: str
    slot_minutes: int = 30
    day_start: str = "09:00"  # local time in the app timezone
    day_end: str = "17:00"
    working_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])  # Mon=0


class Appointment(BaseModel):
    id: str = Field(default_factory=lambda: new_id("apt"))
    provider_id: str
    customer_name: str
    customer_email: str
    start: datetime
    end: datetime
    reason: str = ""
    status: AppointmentStatus = AppointmentStatus.BOOKED
    notes: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and start < self.end


class Reminder(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rem"))
    appointment_id: str
    customer_email: str
    remind_at: datetime
    channel: str = "email"
    message: str = ""
    status: ReminderStatus = ReminderStatus.PENDING
    created_at: datetime = Field(default_factory=utcnow)


class TimeSlot(BaseModel):
    provider_id: str
    start: datetime
    end: datetime
