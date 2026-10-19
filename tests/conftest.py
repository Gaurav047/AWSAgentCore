import os

# Keep a developer's local .env out of the test run; must be set before appointment_app is imported.
os.environ["APP_SKIP_DOTENV"] = "1"

from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402

from appointment_app.domain import AppointmentService  # noqa: E402
from appointment_app.domain.repository import SQLiteRepository

# Monday 19 October 2026, 08:00 UTC
NOW = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)


@pytest.fixture
def service() -> AppointmentService:
    svc = AppointmentService(SQLiteRepository(":memory:"), tz="UTC", clock=lambda: NOW)
    svc.seed_defaults()
    return svc
