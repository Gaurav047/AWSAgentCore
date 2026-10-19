import pytest

from appointment_app.domain import AppointmentService, SlotUnavailableError
from tests.conftest import NOW

moto = pytest.importorskip("moto")


@pytest.fixture
def dynamo_service(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    from appointment_app.domain.repository import DynamoDBRepository

    with moto.mock_aws():
        repo = DynamoDBRepository("appointments-test", region_name="us-east-1")
        repo.ensure_table()
        svc = AppointmentService(repo, tz="UTC", clock=lambda: NOW)
        svc.seed_defaults()
        yield svc


def test_dynamodb_booking_lifecycle(dynamo_service):
    svc = dynamo_service
    appt = svc.book("dr-rao", "Jane", "jane@example.com", "2026-10-20T10:00", duration_minutes=60)
    with pytest.raises(SlotUnavailableError):
        svc.book("dr-rao", "Bob", "bob@example.com", "2026-10-20T10:30")

    # Overlapping reschedule (10:00-11:00 -> 10:30-11:30) must reuse its own 10:30 lock.
    svc.reschedule(appt.id, "2026-10-20T10:30")
    svc.book("dr-rao", "Bob", "bob@example.com", "2026-10-20T10:00")
    with pytest.raises(SlotUnavailableError):
        svc.book("dr-rao", "Eve", "eve@example.com", "2026-10-20T11:00")

    svc.cancel(appt.id)
    svc.book("dr-rao", "Eve", "eve@example.com", "2026-10-20T11:00")
    assert len(svc.list(provider_id="dr-rao")) == 2
    assert len(svc.list(provider_id="dr-rao", include_cancelled=True)) == 3
