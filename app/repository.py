"""Data access layer. The REST routes and the Vapi tool webhooks both go through
here, so the voice agent and an HTTP client can never diverge in behaviour."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from app.db import get_conn

COLUMNS = (
    "patient_id", "first_name", "last_name", "date_of_birth", "sex",
    "phone_number", "email", "address_line_1", "address_line_2", "city",
    "state", "zip_code", "insurance_provider", "insurance_member_id",
    "preferred_language", "emergency_contact_name", "emergency_contact_phone",
    "created_at", "updated_at", "deleted_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _serialize(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def create_patient(data: dict) -> dict:
    record = {k: _serialize(v) for k, v in data.items()}
    record["patient_id"] = str(uuid.uuid4())
    record["created_at"] = record["updated_at"] = _now()
    record["deleted_at"] = None
    record.setdefault("preferred_language", "English")

    cols = [c for c in COLUMNS if c in record]
    placeholders = ", ".join(f":{c}" for c in cols)
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO patients ({', '.join(cols)}) VALUES ({placeholders})",
            {c: record.get(c) for c in cols},
        )
    return get_patient(record["patient_id"])


def get_patient(patient_id: str, include_deleted: bool = False) -> Optional[dict]:
    sql = "SELECT * FROM patients WHERE patient_id = ?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    with get_conn() as conn:
        row = conn.execute(sql, (patient_id,)).fetchone()
    return dict(row) if row else None


def list_patients(
    last_name: Optional[str] = None,
    date_of_birth: Optional[str] = None,
    phone_number: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    sql = "SELECT * FROM patients WHERE deleted_at IS NULL"
    params: list[Any] = []
    if last_name:
        sql += " AND LOWER(last_name) = LOWER(?)"
        params.append(last_name.strip())
    if date_of_birth:
        sql += " AND date_of_birth = ?"
        params.append(date_of_birth)
    if phone_number:
        sql += " AND phone_number = ?"
        params.append(phone_number)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def find_by_phone(phone_number: str) -> Optional[dict]:
    """Used by the agent for duplicate detection on returning callers."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM patients WHERE phone_number = ? AND deleted_at IS NULL"
            " ORDER BY created_at DESC LIMIT 1",
            (phone_number,),
        ).fetchone()
    return dict(row) if row else None


def update_patient(patient_id: str, changes: dict) -> Optional[dict]:
    changes = {k: _serialize(v) for k, v in changes.items() if k in COLUMNS}
    if not changes:
        return get_patient(patient_id)
    changes["updated_at"] = _now()
    assignments = ", ".join(f"{k} = :{k}" for k in changes)
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE patients SET {assignments} WHERE patient_id = :pid AND deleted_at IS NULL",
            {**changes, "pid": patient_id},
        )
        if cur.rowcount == 0:
            return None
    return get_patient(patient_id)


def soft_delete_patient(patient_id: str) -> Optional[dict]:
    stamp = _now()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE patients SET deleted_at = ?, updated_at = ?"
            " WHERE patient_id = ? AND deleted_at IS NULL",
            (stamp, stamp, patient_id),
        )
        if cur.rowcount == 0:
            return None
    return get_patient(patient_id, include_deleted=True)


def list_call_logs(patient_id: Optional[str] = None, limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM call_logs"
    params: list[Any] = []
    if patient_id:
        sql += " WHERE patient_id = ?"
        params.append(patient_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for row in rows:
        row["payload"] = json.loads(row["payload"])
    return rows


def log_call(call_id: Optional[str], patient_id: Optional[str], payload: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO call_logs (call_id, patient_id, payload, created_at)"
            " VALUES (?, ?, ?, ?)",
            (call_id, patient_id, json.dumps(payload, default=str), _now()),
        )
