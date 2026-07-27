"""REST API for patient records. Envelope: {"data": ..., "error": ...}"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status

from app import repository, scheduling
from app.schemas import PatientCreate, PatientUpdate, normalize_date, normalize_phone

log = logging.getLogger("api")
router = APIRouter(prefix="/patients", tags=["patients"])


def ok(data):
    return {"data": data, "error": None}


@router.get("")
def list_patients(
    last_name: Optional[str] = Query(None, max_length=50),
    date_of_birth: Optional[str] = Query(None, description="MM/DD/YYYY or YYYY-MM-DD"),
    phone_number: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    # Normalize filters so a caller can search with any common format.
    try:
        dob = normalize_date(date_of_birth).isoformat() if date_of_birth else None
        phone = normalize_phone(phone_number) if phone_number else None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    rows = repository.list_patients(last_name, dob, phone, limit, offset)
    return ok(rows)


@router.get("/{patient_id}")
def get_patient(patient_id: str):
    row = repository.get_patient(patient_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return ok(row)


@router.get("/{patient_id}/calls")
def get_patient_calls(patient_id: str):
    """Call transcripts and registration payloads linked to this patient."""
    if not repository.get_patient(patient_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return ok(repository.list_call_logs(patient_id))


@router.get("/{patient_id}/appointments")
def get_patient_appointments(patient_id: str):
    if not repository.get_patient(patient_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return ok(scheduling.list_appointments(patient_id))


@router.post("", status_code=status.HTTP_201_CREATED)
def create_patient(payload: PatientCreate):
    row = repository.create_patient(payload.model_dump())
    log.info("patient.created id=%s name=%s %s", row["patient_id"],
             row["first_name"], row["last_name"])
    return ok(row)


@router.put("/{patient_id}")
def update_patient(patient_id: str, payload: PatientUpdate):
    changes = payload.model_dump(exclude_unset=True)
    row = repository.update_patient(patient_id, changes)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Patient not found")
    log.info("patient.updated id=%s fields=%s", patient_id, list(changes))
    return ok(row)


@router.delete("/{patient_id}")
def delete_patient(patient_id: str):
    row = repository.soft_delete_patient(patient_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Patient not found or already deleted")
    log.info("patient.deleted id=%s", patient_id)
    return ok(row)
