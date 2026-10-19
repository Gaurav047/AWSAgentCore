import threading
from datetime import timedelta

import pytest

from appointment_app.domain import AppointmentStatus, NotFoundError, SlotUnavailableError, ValidationError


def book(service, start="2026-10-20T10:00", provider="dr-rao", email="jane@example.com"):
    return service.book(provider, "Jane Doe", email, start, "checkup")


def test_slots_exclude_booked_and_past(service):
    today = service.find_available_slots("dr-rao", "2026-10-19")
    assert today[0].start.hour == 9  # clock is 08:00, day starts 09:00
    book(service, "2026-10-19T09:00")
    starts = [s.start.strftime("%H:%M") for s in service.find_available_slots("dr-rao", "2026-10-19")]
    assert "09:00" not in starts and "09:30" in starts
    assert len(starts) == 15  # 16 half-hour slots between 09:00-17:00, one taken


def test_no_slots_on_non_working_day(service):
    assert service.find_available_slots("dr-rao", "2026-10-25") == []  # Sunday


def test_book_and_get(service):
    appt = book(service)
    assert appt.status == AppointmentStatus.BOOKED
    assert appt.end - appt.start == timedelta(minutes=30)
    assert service.get(appt.id).customer_email == "jane@example.com"


def test_double_booking_rejected_with_alternatives(service):
    book(service)
    with pytest.raises(SlotUnavailableError, match="Next openings"):
        book(service, email="bob@example.com")


def test_overlapping_longer_appointment_rejected(service):
    service.book("dr-rao", "A", "a@example.com", "2026-10-20T10:00", duration_minutes=60)
    with pytest.raises(SlotUnavailableError):
        book(service, "2026-10-20T10:30")


@pytest.mark.parametrize(
    "start,match",
    [
        ("2026-10-18T10:00", "future"),
        ("2026-10-24T10:00", "does not work"),
        ("2026-10-20T07:00", "between"),
        ("2026-10-20T16:45", "align|between"),
        ("2026-10-20T10:15", "align"),
        ("not-a-date", "Invalid datetime"),
    ],
)
def test_validation(service, start, match):
    with pytest.raises(ValidationError, match=match):
        book(service, start)


def test_invalid_email_and_unknown_provider(service):
    with pytest.raises(ValidationError):
        book(service, email="nope")
    with pytest.raises(NotFoundError, match="Known providers"):
        book(service, provider="dr-who")


def test_cancel_frees_slot_and_cancels_reminders(service):
    appt = book(service)
    service.schedule_reminder(appt.id, 60)
    service.cancel(appt.id, "sick")
    assert all(r.status == "cancelled" for r in service.list_reminders(appt.id))
    book(service, email="bob@example.com")  # slot is free again
    with pytest.raises(ValidationError):
        service.cancel(appt.id)


def test_reschedule_moves_reminders(service):
    appt = book(service)
    reminder = service.schedule_reminder(appt.id, 60)
    moved = service.reschedule(appt.id, "2026-10-21T14:00")
    assert moved.start.isoformat().startswith("2026-10-21T14:00")
    (updated,) = service.list_reminders(appt.id)
    assert updated.remind_at - reminder.remind_at == moved.start - appt.start


def test_reschedule_into_conflict(service):
    a = book(service)
    book(service, "2026-10-20T11:00", email="bob@example.com")
    with pytest.raises(SlotUnavailableError):
        service.reschedule(a.id, "2026-10-20T11:00")


def test_list_filters(service):
    book(service)
    book(service, "2026-10-21T10:00", provider="dr-santos", email="bob@example.com")
    assert len(service.list(customer_email="JANE@example.com")) == 1
    assert len(service.list(day="2026-10-21")) == 1
    assert len(service.list(provider_id="dr-rao")) == 1


def test_reminder_rules_and_dispatch(service):
    appt = book(service, "2026-10-19T10:00")
    with pytest.raises(ValidationError, match="passed"):
        service.schedule_reminder(appt.id, 24 * 60)
    with pytest.raises(ValidationError, match="channel"):
        service.schedule_reminder(appt.id, 60, channel="pigeon")
    service.schedule_reminder(appt.id, 90, channel="sms")  # due at 08:30
    sent = []
    service.clock = lambda: appt.start - timedelta(minutes=30)
    assert len(service.dispatch_due_reminders(sent.append)) == 1
    assert sent[0].channel == "sms" and "Dr. Asha Rao" in sent[0].message
    assert service.dispatch_due_reminders(sent.append) == []


def test_concurrent_booking_only_one_wins(service):
    results = []

    def attempt(i):
        try:
            book(service, email=f"user{i}@example.com")
            results.append("ok")
        except SlotUnavailableError:
            results.append("conflict")

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1
