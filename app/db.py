"""SQLite connection handling and schema bootstrap.

Plain stdlib sqlite3 is deliberate: a single-file DB that survives restarts is
all this assessment needs, and it keeps the dependency surface tiny. Swapping in
Postgres later means rewriting only this module and `repository.py`.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATABASE_PATH = os.getenv("DATABASE_PATH", "patients.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    patient_id              TEXT PRIMARY KEY,
    first_name              TEXT    NOT NULL,
    last_name               TEXT    NOT NULL,
    date_of_birth           TEXT    NOT NULL,            -- ISO YYYY-MM-DD
    sex                     TEXT    NOT NULL CHECK (sex IN ('Male','Female','Other','Decline to Answer')),
    phone_number            TEXT    NOT NULL,            -- normalized to 10 digits
    email                   TEXT,
    address_line_1          TEXT    NOT NULL,
    address_line_2          TEXT,
    city                    TEXT    NOT NULL,
    state                   TEXT    NOT NULL,            -- 2-letter abbreviation
    zip_code                TEXT    NOT NULL,
    insurance_provider      TEXT,
    insurance_member_id     TEXT,
    preferred_language      TEXT    NOT NULL DEFAULT 'English',
    emergency_contact_name  TEXT,
    emergency_contact_phone TEXT,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    deleted_at              TEXT
);

CREATE INDEX IF NOT EXISTS idx_patients_phone     ON patients(phone_number);
CREATE INDEX IF NOT EXISTS idx_patients_last_name ON patients(last_name);
CREATE INDEX IF NOT EXISTS idx_patients_dob       ON patients(date_of_birth);

-- One row per completed call, for observability / the transcript bonus.
CREATE TABLE IF NOT EXISTS call_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id    TEXT,
    patient_id TEXT,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_call_logs_patient ON call_logs(patient_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_call    ON call_logs(call_id);

-- Mock appointment booking. Slots are generated on the fly (see services/
-- scheduling.py); this table only records what was actually booked, so a slot
-- is "taken" iff a non-cancelled row references it.
CREATE TABLE IF NOT EXISTS appointments (
    appointment_id TEXT PRIMARY KEY,
    patient_id     TEXT NOT NULL REFERENCES patients(patient_id),
    slot_id        TEXT NOT NULL,
    starts_at      TEXT NOT NULL,
    provider       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','cancelled')),
    created_at     TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_appt_slot_unique
    ON appointments(slot_id) WHERE status = 'booked';
CREATE INDEX IF NOT EXISTS idx_appt_patient ON appointments(patient_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_conn():
    """Short-lived connection per request; commits on success, rolls back on error."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    parent = Path(DATABASE_PATH).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
