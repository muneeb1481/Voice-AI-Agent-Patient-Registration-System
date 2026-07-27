"""Mock appointment scheduling.

No external calendar. Slots are *derived* from the current date rather than
stored, so the demo never runs out of availability and there's nothing to seed:
the next few weekdays x a fixed set of times, minus whatever is already booked.
Swapping in a real calendar means replacing `available_slots()` only.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.db import get_conn

PROVIDER = "Dr. Amara Osei"
CLINIC_TIMES = ("09:00", "11:30", "14:00", "16:30")
DAYS_AHEAD = 5          # how far out to look
MAX_SLOTS = 3           # how many to offer the caller — three is speakable, ten is not


def _slot_id(day: date, hhmm: str) -> str:
    return f"{day.isoformat()}T{hhmm}"


def _spoken(day: date, hhmm: str) -> str:
    """'Tuesday, July 29 at 9:00 AM' — TTS reads this cleanly.

    Zero-stripping is done by hand because the %-d / %#d strftime flag differs
    between glibc and Windows.
    """
    stamp = datetime.strptime(f"{day.isoformat()} {hhmm}", "%Y-%m-%d %H:%M")
    return (f"{stamp.strftime('%A')}, {stamp.strftime('%B')} {stamp.day} at "
            f"{stamp.hour % 12 or 12}:{stamp.strftime('%M %p')}")


def _booked_slot_ids() -> set[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT slot_id FROM appointments WHERE status = 'booked'"
        ).fetchall()
    return {r["slot_id"] for r in rows}


def available_slots(limit: int = MAX_SLOTS) -> list[dict]:
    taken = _booked_slot_ids()
    today = datetime.now(timezone.utc).date()
    out: list[dict] = []

    for offset in range(1, DAYS_AHEAD + 1):
        day = today + timedelta(days=offset)
        if day.weekday() >= 5:          # clinic is closed on weekends
            continue
        for hhmm in CLINIC_TIMES:
            sid = _slot_id(day, hhmm)
            if sid in taken:
                continue
            out.append({"slot_id": sid, "starts_at": sid,
                        "when": _spoken(day, hhmm), "provider": PROVIDER})
            if len(out) >= limit:
                return out
    return out


def book_slot(patient_id: str, slot_id: str) -> dict:
    """Returns {"status": ...} — 'booked', 'unavailable', or 'invalid_slot'."""
    try:
        day_str, hhmm = slot_id.split("T")
        day = date.fromisoformat(day_str)
        datetime.strptime(hhmm, "%H:%M")
    except (ValueError, AttributeError):
        return {"status": "invalid_slot"}

    if slot_id in _booked_slot_ids():
        return {"status": "unavailable"}

    record = {
        "appointment_id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "slot_id": slot_id,
        "starts_at": slot_id,
        "provider": PROVIDER,
        "status": "booked",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO appointments (appointment_id, patient_id, slot_id, starts_at,"
            " provider, status, created_at) VALUES (:appointment_id, :patient_id,"
            " :slot_id, :starts_at, :provider, :status, :created_at)",
            record,
        )
    return {"status": "booked", "when": _spoken(day, hhmm), **record}


def list_appointments(patient_id: Optional[str] = None) -> list[dict]:
    sql = ("SELECT a.*, p.first_name, p.last_name FROM appointments a"
           " JOIN patients p ON p.patient_id = a.patient_id")
    params: list = []
    if patient_id:
        sql += " WHERE a.patient_id = ?"
        params.append(patient_id)
    sql += " ORDER BY a.starts_at"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
