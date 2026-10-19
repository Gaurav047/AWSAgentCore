"""Business rules for scheduling. Every interface (MCP, REST, agents) goes through this class."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .errors import NotFoundError, SlotUnavailableError, ValidationError
from .models import Appointment, AppointmentStatus, Provider, Reminder, ReminderStatus, TimeSlot, utcnow
from .repository import AppointmentRepository, DynamoDBRepository, SQLiteRepository

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CHANNELS = {"email", "sms"}

DEFAULT_PROVIDERS = [
    Provider(id="dr-rao", name="Dr. Asha Rao", specialty="general practice", slot_minutes=30),
    Provider(id="dr-santos", name="Dr. Miguel Santos", specialty="dentistry", slot_minutes=60, day_start="08:00"),
    Provider(
        id="dr-fischer",
        name="Dr. Lena Fischer",
        specialty="dermatology",
        slot_minutes=30,
        day_start="10:00",
        day_end="18:00",
        working_days=[1, 2, 3, 4, 5],
    ),
]


def _hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


class AppointmentService:
    def __init__(
        self,
        repo: AppointmentRepository,
        tz: ZoneInfo | str = "UTC",
        clock: Callable[[], datetime] = utcnow,
    ):
        self.repo = repo
        self.tz = ZoneInfo(tz) if isinstance(tz, str) else tz
        self.clock = clock

    # ------------------------------------------------------------------ helpers

    def now(self) -> datetime:
        return self.clock().astimezone(self.tz)

    def parse_datetime(self, value: str | datetime) -> datetime:
        """Accept ISO-8601; naive values are interpreted in the app timezone."""
        if isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValidationError(f"Invalid datetime '{value}'. Use ISO-8601, e.g. 2026-10-21T14:30") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return dt.astimezone(self.tz)

    def parse_date(self, value: str | date) -> date:
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValidationError(f"Invalid date '{value}'. Use YYYY-MM-DD") from exc

    def _provider(self, provider_id: str) -> Provider:
        provider = self.repo.get_provider(provider_id)
        if provider is None:
            known = ", ".join(p.id for p in self.repo.list_providers())
            raise NotFoundError(f"Unknown provider '{provider_id}'. Known providers: {known}")
        return provider

    def _appointment(self, appointment_id: str) -> Appointment:
        appt = self.repo.get_appointment(appointment_id)
        if appt is None:
            raise NotFoundError(f"Appointment '{appointment_id}' not found")
        return appt

    def _localize(self, appt: Appointment) -> Appointment:
        return appt.model_copy(update={"start": appt.start.astimezone(self.tz), "end": appt.end.astimezone(self.tz)})

    def _day_bounds(self, provider: Provider, day: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(day, _hhmm(provider.day_start), self.tz),
            datetime.combine(day, _hhmm(provider.day_end), self.tz),
        )

    def _validate_window(self, provider: Provider, start: datetime, duration_minutes: int) -> datetime:
        if duration_minutes <= 0 or duration_minutes % provider.slot_minutes:
            raise ValidationError(f"Duration must be a positive multiple of {provider.slot_minutes} minutes for {provider.name}")
        end = start + timedelta(minutes=duration_minutes)
        if start <= self.now():
            raise ValidationError("Appointments must be in the future")
        if start.weekday() not in provider.working_days:
            raise ValidationError(f"{provider.name} does not work on {start:%A}s")
        day_start, day_end = self._day_bounds(provider, start.date())
        if start < day_start or end > day_end:
            raise ValidationError(f"{provider.name} sees patients between {provider.day_start} and {provider.day_end}")
        if (start - day_start) % timedelta(minutes=provider.slot_minutes):
            raise ValidationError(f"Start times must align to {provider.slot_minutes}-minute slots from {provider.day_start}")
        return end

    # ------------------------------------------------------------------ providers

    def seed_defaults(self) -> None:
        if not self.repo.list_providers():
            for provider in DEFAULT_PROVIDERS:
                self.repo.upsert_provider(provider)

    def list_providers(self, specialty: str | None = None) -> list[Provider]:
        providers = self.repo.list_providers()
        if specialty:
            needle = specialty.lower()
            providers = [p for p in providers if needle in p.specialty.lower() or needle in p.name.lower()]
        return providers

    # ------------------------------------------------------------------ availability

    def find_available_slots(
        self, provider_id: str, day: str | date, duration_minutes: int | None = None
    ) -> list[TimeSlot]:
        provider = self._provider(provider_id)
        the_day = self.parse_date(day)
        duration = timedelta(minutes=duration_minutes or provider.slot_minutes)
        if the_day.weekday() not in provider.working_days:
            return []
        day_start, day_end = self._day_bounds(provider, the_day)
        booked = self.repo.list_appointments(
            provider_id=provider_id,
            start_from=day_start - timedelta(days=1),
            start_to=day_end,
            statuses=[AppointmentStatus.BOOKED],
        )
        now, step = self.now(), timedelta(minutes=provider.slot_minutes)
        slots, cursor = [], day_start
        while cursor + duration <= day_end:
            end = cursor + duration
            if cursor > now and not any(a.overlaps(cursor, end) for a in booked):
                slots.append(TimeSlot(provider_id=provider_id, start=cursor, end=end))
            cursor += step
        return slots

    def next_available_slots(self, provider_id: str, days_ahead: int = 14, limit: int = 5) -> list[TimeSlot]:
        today, found = self.now().date(), []
        for offset in range(days_ahead):
            found.extend(self.find_available_slots(provider_id, today + timedelta(days=offset)))
            if len(found) >= limit:
                break
        return found[:limit]

    # ------------------------------------------------------------------ appointments

    def book(
        self,
        provider_id: str,
        customer_name: str,
        customer_email: str,
        start: str | datetime,
        reason: str = "",
        duration_minutes: int | None = None,
    ) -> Appointment:
        provider = self._provider(provider_id)
        if not customer_name.strip():
            raise ValidationError("Customer name is required")
        if not _EMAIL_RE.match(customer_email.strip()):
            raise ValidationError(f"'{customer_email}' is not a valid email address")
        start_dt = self.parse_datetime(start)
        end_dt = self._validate_window(provider, start_dt, duration_minutes or provider.slot_minutes)
        appt = Appointment(
            provider_id=provider_id,
            customer_name=customer_name.strip(),
            customer_email=customer_email.strip().lower(),
            start=start_dt,
            end=end_dt,
            reason=reason.strip(),
        )
        try:
            return self._localize(self.repo.create_appointment(appt))
        except SlotUnavailableError as exc:
            alternatives = ", ".join(f"{s.start:%Y-%m-%d %H:%M}" for s in self.next_available_slots(provider_id, limit=3))
            raise SlotUnavailableError(f"{exc}. Next openings with {provider.name}: {alternatives or 'none in the next 2 weeks'}") from exc

    def cancel(self, appointment_id: str, reason: str = "") -> Appointment:
        appt = self._appointment(appointment_id)
        if appt.status != AppointmentStatus.BOOKED:
            raise ValidationError(f"Appointment {appointment_id} is already {appt.status.value}")
        updated = appt.model_copy(
            update={
                "status": AppointmentStatus.CANCELLED,
                "notes": (appt.notes + f"\nCancelled: {reason}").strip() if reason else appt.notes,
                "updated_at": utcnow(),
            }
        )
        self.repo.replace_appointment(appt, updated)
        for reminder in self.repo.list_reminders(appointment_id=appointment_id, status=ReminderStatus.PENDING):
            self.repo.save_reminder(reminder.model_copy(update={"status": ReminderStatus.CANCELLED}))
        return self._localize(updated)

    def reschedule(self, appointment_id: str, new_start: str | datetime) -> Appointment:
        appt = self._appointment(appointment_id)
        if appt.status != AppointmentStatus.BOOKED:
            raise ValidationError(f"Cannot reschedule an appointment that is {appt.status.value}")
        provider = self._provider(appt.provider_id)
        start_dt = self.parse_datetime(new_start)
        duration = int((appt.end - appt.start).total_seconds() // 60)
        end_dt = self._validate_window(provider, start_dt, duration)
        updated = appt.model_copy(update={"start": start_dt, "end": end_dt, "updated_at": utcnow()})
        self.repo.replace_appointment(appt, updated)
        # Shift pending reminders by the same delta so they stay relative to the appointment.
        delta = start_dt - appt.start
        for reminder in self.repo.list_reminders(appointment_id=appointment_id, status=ReminderStatus.PENDING):
            self.repo.save_reminder(reminder.model_copy(update={"remind_at": reminder.remind_at + delta}))
        return self._localize(updated)

    def get(self, appointment_id: str) -> Appointment:
        return self._localize(self._appointment(appointment_id))

    def list(
        self,
        customer_email: str | None = None,
        provider_id: str | None = None,
        day: str | date | None = None,
        include_cancelled: bool = False,
        upcoming_only: bool = False,
    ) -> list[Appointment]:
        start_from = start_to = None
        if day:
            the_day = self.parse_date(day)
            start_from = datetime.combine(the_day, time.min, self.tz)
            start_to = start_from + timedelta(days=1)
        if upcoming_only:
            start_from = max(start_from, self.now()) if start_from else self.now()
        statuses = None if include_cancelled else [AppointmentStatus.BOOKED, AppointmentStatus.COMPLETED]
        items = self.repo.list_appointments(
            provider_id=provider_id,
            customer_email=customer_email,
            start_from=start_from,
            start_to=start_to,
            statuses=statuses,
        )
        return [self._localize(a) for a in items]

    # ------------------------------------------------------------------ reminders

    def schedule_reminder(
        self, appointment_id: str, minutes_before: int = 1440, channel: str = "email", message: str = ""
    ) -> Reminder:
        appt = self._appointment(appointment_id)
        if appt.status != AppointmentStatus.BOOKED:
            raise ValidationError("Reminders can only be scheduled for booked appointments")
        if channel not in _CHANNELS:
            raise ValidationError(f"Unsupported channel '{channel}'. Use one of: {', '.join(sorted(_CHANNELS))}")
        if minutes_before <= 0:
            raise ValidationError("minutes_before must be positive")
        remind_at = appt.start - timedelta(minutes=minutes_before)
        if remind_at <= self.clock():
            raise ValidationError("That reminder time has already passed")
        provider = self._provider(appt.provider_id)
        local_start = appt.start.astimezone(self.tz)
        text = message.strip() or (
            f"Hi {appt.customer_name}, this is a reminder of your appointment with {provider.name} "
            f"on {local_start:%A %d %B at %H:%M} ({self.tz.key})."
        )
        reminder = Reminder(
            appointment_id=appointment_id,
            customer_email=appt.customer_email,
            remind_at=remind_at.astimezone(UTC),
            channel=channel,
            message=text,
        )
        return self.repo.save_reminder(reminder)

    def list_reminders(self, appointment_id: str | None = None, customer_email: str | None = None) -> list[Reminder]:
        return self.repo.list_reminders(appointment_id=appointment_id, customer_email=customer_email)

    def dispatch_due_reminders(self, send: Callable[[Reminder], None]) -> list[Reminder]:
        """Deliver every pending reminder that is due. Call this from a scheduler (e.g. EventBridge)."""
        sent = []
        for reminder in self.repo.list_reminders(status=ReminderStatus.PENDING, due_before=self.clock()):
            send(reminder)
            sent.append(self.repo.save_reminder(reminder.model_copy(update={"status": ReminderStatus.SENT})))
        return sent


def build_service(settings=None) -> AppointmentService:
    from ..config import get_settings

    settings = settings or get_settings()
    if settings.storage_backend == "dynamodb":
        repo: AppointmentRepository = DynamoDBRepository(settings.dynamodb_table, region_name=settings.aws_region)
    else:
        repo = SQLiteRepository(settings.sqlite_path)
    service = AppointmentService(repo, tz=settings.tz)
    service.seed_defaults()
    return service
