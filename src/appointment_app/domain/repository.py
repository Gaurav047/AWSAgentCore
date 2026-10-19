"""Storage backends.

Both backends guarantee that two *booked* appointments for the same provider never overlap,
even under concurrent writers:

* SQLite  - overlap check + insert inside a `BEGIN IMMEDIATE` transaction.
* DynamoDB - one lock item per provider time-slot, written with `attribute_not_exists`
  conditions inside a `TransactWriteItems` call.
"""

from __future__ import annotations

import sqlite3
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from .errors import NotFoundError, SlotUnavailableError
from .models import Appointment, AppointmentStatus, Provider, Reminder, ReminderStatus


def _utc_key(dt: datetime) -> str:
    """Sortable, offset-free representation used for storage and range comparisons."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class AppointmentRepository(ABC):
    @abstractmethod
    def upsert_provider(self, provider: Provider) -> None: ...

    @abstractmethod
    def get_provider(self, provider_id: str) -> Provider | None: ...

    @abstractmethod
    def list_providers(self) -> list[Provider]: ...

    @abstractmethod
    def create_appointment(self, appointment: Appointment) -> Appointment:
        """Insert atomically; raise SlotUnavailableError if it overlaps a booked appointment."""

    @abstractmethod
    def replace_appointment(self, old: Appointment, new: Appointment) -> Appointment:
        """Atomically replace `old` with `new` (same id), re-checking conflicts if `new` is booked."""

    @abstractmethod
    def get_appointment(self, appointment_id: str) -> Appointment | None: ...

    @abstractmethod
    def list_appointments(
        self,
        *,
        provider_id: str | None = None,
        customer_email: str | None = None,
        start_from: datetime | None = None,
        start_to: datetime | None = None,
        statuses: Iterable[AppointmentStatus] | None = None,
    ) -> list[Appointment]: ...

    @abstractmethod
    def save_reminder(self, reminder: Reminder) -> Reminder: ...

    @abstractmethod
    def list_reminders(
        self,
        *,
        appointment_id: str | None = None,
        customer_email: str | None = None,
        status: ReminderStatus | None = None,
        due_before: datetime | None = None,
    ) -> list[Reminder]: ...


def _filter_appointments(
    items: Iterable[Appointment],
    provider_id: str | None,
    customer_email: str | None,
    start_from: datetime | None,
    start_to: datetime | None,
    statuses: Iterable[AppointmentStatus] | None,
) -> list[Appointment]:
    wanted = set(statuses) if statuses else None
    out = [
        a
        for a in items
        if (provider_id is None or a.provider_id == provider_id)
        and (customer_email is None or a.customer_email.lower() == customer_email.lower())
        and (start_from is None or a.start >= start_from)
        and (start_to is None or a.start < start_to)
        and (wanted is None or a.status in wanted)
    ]
    return sorted(out, key=lambda a: a.start)


def _filter_reminders(
    items: Iterable[Reminder],
    appointment_id: str | None,
    customer_email: str | None,
    status: ReminderStatus | None,
    due_before: datetime | None,
) -> list[Reminder]:
    out = [
        r
        for r in items
        if (appointment_id is None or r.appointment_id == appointment_id)
        and (customer_email is None or r.customer_email.lower() == customer_email.lower())
        and (status is None or r.status == status)
        and (due_before is None or r.remind_at <= due_before)
    ]
    return sorted(out, key=lambda r: r.remind_at)


# --------------------------------------------------------------------------- SQLite


class SQLiteRepository(AppointmentRepository):
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS providers (id TEXT PRIMARY KEY, data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS appointments (
        id TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL,
        customer_email TEXT NOT NULL,
        start_utc TEXT NOT NULL,
        end_utc TEXT NOT NULL,
        status TEXT NOT NULL,
        data TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_appt_provider ON appointments (provider_id, status, start_utc);
    CREATE INDEX IF NOT EXISTS ix_appt_customer ON appointments (customer_email);
    CREATE TABLE IF NOT EXISTS reminders (
        id TEXT PRIMARY KEY,
        appointment_id TEXT NOT NULL,
        remind_at_utc TEXT NOT NULL,
        status TEXT NOT NULL,
        data TEXT NOT NULL
    );
    """

    def __init__(self, path: str = ":memory:"):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(self._SCHEMA)

    # providers
    def upsert_provider(self, provider: Provider) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO providers (id, data) VALUES (?, ?)",
                (provider.id, provider.model_dump_json()),
            )

    def get_provider(self, provider_id: str) -> Provider | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM providers WHERE id = ?", (provider_id,)).fetchone()
        return Provider.model_validate_json(row[0]) if row else None

    def list_providers(self) -> list[Provider]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM providers ORDER BY id").fetchall()
        return [Provider.model_validate_json(r[0]) for r in rows]

    # appointments
    def _has_conflict(self, appt: Appointment) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM appointments WHERE provider_id = ? AND status = ? AND id != ? "
            "AND start_utc < ? AND end_utc > ? LIMIT 1",
            (appt.provider_id, AppointmentStatus.BOOKED.value, appt.id, _utc_key(appt.end), _utc_key(appt.start)),
        ).fetchone()
        return row is not None

    def _write(self, appt: Appointment) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO appointments (id, provider_id, customer_email, start_utc, end_utc, status, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                appt.id,
                appt.provider_id,
                appt.customer_email.lower(),
                _utc_key(appt.start),
                _utc_key(appt.end),
                appt.status.value,
                appt.model_dump_json(),
            ),
        )

    def _transaction(self, appt: Appointment, check_conflict: bool, must_exist: bool) -> Appointment:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                exists = self._conn.execute("SELECT 1 FROM appointments WHERE id = ?", (appt.id,)).fetchone()
                if must_exist and not exists:
                    raise NotFoundError(f"Appointment {appt.id} not found")
                if not must_exist and exists:
                    raise SlotUnavailableError(f"Appointment {appt.id} already exists")
                if check_conflict and self._has_conflict(appt):
                    raise SlotUnavailableError("That time overlaps an existing appointment")
                self._write(appt)
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
        return appt

    def create_appointment(self, appointment: Appointment) -> Appointment:
        return self._transaction(appointment, check_conflict=True, must_exist=False)

    def replace_appointment(self, old: Appointment, new: Appointment) -> Appointment:
        return self._transaction(new, check_conflict=new.status == AppointmentStatus.BOOKED, must_exist=True)

    def get_appointment(self, appointment_id: str) -> Appointment | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM appointments WHERE id = ?", (appointment_id,)).fetchone()
        return Appointment.model_validate_json(row[0]) if row else None

    def list_appointments(self, *, provider_id=None, customer_email=None, start_from=None, start_to=None, statuses=None):
        sql, args = "SELECT data FROM appointments WHERE 1=1", []
        if provider_id:
            sql += " AND provider_id = ?"
            args.append(provider_id)
        if customer_email:
            sql += " AND customer_email = ?"
            args.append(customer_email.lower())
        if start_from:
            sql += " AND start_utc >= ?"
            args.append(_utc_key(start_from))
        if start_to:
            sql += " AND start_utc < ?"
            args.append(_utc_key(start_to))
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY start_utc", args).fetchall()
        items = [Appointment.model_validate_json(r[0]) for r in rows]
        return _filter_appointments(items, None, None, None, None, statuses)

    # reminders
    def save_reminder(self, reminder: Reminder) -> Reminder:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO reminders (id, appointment_id, remind_at_utc, status, data) VALUES (?, ?, ?, ?, ?)",
                (
                    reminder.id,
                    reminder.appointment_id,
                    _utc_key(reminder.remind_at),
                    reminder.status.value,
                    reminder.model_dump_json(),
                ),
            )
        return reminder

    def list_reminders(self, *, appointment_id=None, customer_email=None, status=None, due_before=None):
        with self._lock:
            rows = self._conn.execute("SELECT data FROM reminders").fetchall()
        items = [Reminder.model_validate_json(r[0]) for r in rows]
        return _filter_reminders(items, appointment_id, customer_email, status, due_before)


# --------------------------------------------------------------------------- DynamoDB


class DynamoDBRepository(AppointmentRepository):
    """Single-table design.

    pk="PROVIDER"            sk=<provider_id>        provider record
    pk="APPT"                sk=<appointment_id>     appointment record
    pk="LOCK#<provider_id>"  sk=<slot start, UTC>    one item per occupied slot (double-booking guard)
    pk="REMINDER"            sk=<reminder_id>        reminder record

    Listing queries a single partition and filters client-side, which is fine for a
    demo-scale workload; add GSIs (provider/date, customer_email) for production volumes.
    """

    def __init__(self, table_name: str, client=None, region_name: str | None = None):
        import boto3

        self.table = table_name
        self.client = client or boto3.client("dynamodb", region_name=region_name)

    def ensure_table(self) -> None:
        existing = self.client.list_tables().get("TableNames", [])
        if self.table in existing:
            return
        self.client.create_table(
            TableName=self.table,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        self.client.get_waiter("table_exists").wait(TableName=self.table)

    @staticmethod
    def _item(pk: str, sk: str, data: str | None = None, **extra: str) -> dict:
        item = {"pk": {"S": pk}, "sk": {"S": sk}}
        if data is not None:
            item["data"] = {"S": data}
        item.update({k: {"S": v} for k, v in extra.items()})
        return item

    def _query(self, pk: str) -> list[str]:
        out, kwargs = [], {
            "TableName": self.table,
            "KeyConditionExpression": "pk = :pk",
            "ExpressionAttributeValues": {":pk": {"S": pk}},
        }
        while True:
            resp = self.client.query(**kwargs)
            out.extend(i["data"]["S"] for i in resp.get("Items", []) if "data" in i)
            if "LastEvaluatedKey" not in resp:
                return out
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def _get(self, pk: str, sk: str) -> str | None:
        resp = self.client.get_item(TableName=self.table, Key={"pk": {"S": pk}, "sk": {"S": sk}})
        item = resp.get("Item")
        return item["data"]["S"] if item else None

    # providers
    def upsert_provider(self, provider: Provider) -> None:
        self.client.put_item(TableName=self.table, Item=self._item("PROVIDER", provider.id, provider.model_dump_json()))

    def get_provider(self, provider_id: str) -> Provider | None:
        data = self._get("PROVIDER", provider_id)
        return Provider.model_validate_json(data) if data else None

    def list_providers(self) -> list[Provider]:
        return sorted((Provider.model_validate_json(d) for d in self._query("PROVIDER")), key=lambda p: p.id)

    # appointments
    def _lock_keys(self, appt: Appointment) -> set[tuple[str, str]]:
        if appt.status != AppointmentStatus.BOOKED:
            return set()
        provider = self.get_provider(appt.provider_id)
        step = timedelta(minutes=provider.slot_minutes if provider else 15)
        keys, cursor = set(), appt.start
        while cursor < appt.end:
            keys.add((f"LOCK#{appt.provider_id}", _utc_key(cursor)))
            cursor += step
        return keys

    def _commit(self, ops: list[dict]) -> None:
        try:
            self.client.transact_write_items(TransactItems=ops)
        except self.client.exceptions.TransactionCanceledException as exc:
            raise SlotUnavailableError("That time overlaps an existing appointment") from exc

    def create_appointment(self, appointment: Appointment) -> Appointment:
        ops = [
            {
                "Put": {
                    "TableName": self.table,
                    "Item": self._item("APPT", appointment.id, appointment.model_dump_json()),
                    "ConditionExpression": "attribute_not_exists(pk)",
                }
            }
        ]
        for pk, sk in self._lock_keys(appointment):
            ops.append(
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": self._item(pk, sk, appointment_id=appointment.id),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                }
            )
        self._commit(ops)
        return appointment

    def replace_appointment(self, old: Appointment, new: Appointment) -> Appointment:
        old_keys, new_keys = self._lock_keys(old), self._lock_keys(new)
        ops = [
            {
                "Put": {
                    "TableName": self.table,
                    "Item": self._item("APPT", new.id, new.model_dump_json()),
                    "ConditionExpression": "attribute_exists(pk)",
                }
            }
        ]
        for pk, sk in old_keys - new_keys:
            ops.append({"Delete": {"TableName": self.table, "Key": {"pk": {"S": pk}, "sk": {"S": sk}}}})
        for pk, sk in new_keys - old_keys:
            ops.append(
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": self._item(pk, sk, appointment_id=new.id),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                }
            )
        self._commit(ops)
        return new

    def get_appointment(self, appointment_id: str) -> Appointment | None:
        data = self._get("APPT", appointment_id)
        return Appointment.model_validate_json(data) if data else None

    def list_appointments(self, *, provider_id=None, customer_email=None, start_from=None, start_to=None, statuses=None):
        items = (Appointment.model_validate_json(d) for d in self._query("APPT"))
        return _filter_appointments(items, provider_id, customer_email, start_from, start_to, statuses)

    # reminders
    def save_reminder(self, reminder: Reminder) -> Reminder:
        self.client.put_item(TableName=self.table, Item=self._item("REMINDER", reminder.id, reminder.model_dump_json()))
        return reminder

    def list_reminders(self, *, appointment_id=None, customer_email=None, status=None, due_before=None):
        items = (Reminder.model_validate_json(d) for d in self._query("REMINDER"))
        return _filter_reminders(items, appointment_id, customer_email, status, due_before)
