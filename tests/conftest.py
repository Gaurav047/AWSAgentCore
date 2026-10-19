from datetime import UTC, datetime

import pytest

from appointment_app.domain import AppointmentService
from appointment_app.domain.repository import SQLiteRepository

# Monday 19 October 2026, 08:00 UTC
NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)


@pytest.fixture
def service() -> AppointmentService:
    svc = AppointmentService(SQLiteRepository(":memory:"), tz="UTC", clock=lambda: NOW)
    svc.seed_defaults()
    return svc
