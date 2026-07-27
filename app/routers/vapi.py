"""Vapi webhook surface.

Vapi posts every tool invocation to a single URL and expects
    {"results": [{"toolCallId": "<id>", "result": "<string the LLM will read>"}]}

Design notes:
- Results are short, plain-English strings. The LLM reads them and speaks in its
  own words, so we return *facts and instructions*, never fully-formed dialogue.
- Validation errors come back as a `needs_correction` result naming the exact
  field, which is what makes the agent re-prompt for that one field instead of
  restarting the whole intake.
- Every handler is total: an unexpected exception still returns a 200 with an
  apologetic result, because a 500 makes the agent go silent on the caller.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import ValidationError

from app import repository, scheduling
from app.schemas import PatientCreate, PatientUpdate, normalize_phone

log = logging.getLogger("vapi")
router = APIRouter(prefix="/vapi", tags=["vapi"])

VAPI_SERVER_SECRET = os.getenv("VAPI_SERVER_SECRET", "")

# Vapi's end-of-call report arrives after the call is over and carries no
# patient_id, so we remember which record each call created. In-memory is
# deliberate: losing the link on restart costs a transcript association, not
# patient data, and it saves a table for a 3-hour build.
CALL_TO_PATIENT: dict[str, str] = {}


def _authorize(secret: Optional[str]) -> None:
    if VAPI_SERVER_SECRET and secret != VAPI_SERVER_SECRET:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook secret")


def _spoken_summary(p: dict) -> str:
    """Compact, speech-friendly rendering used in read-backs and duplicate hits."""
    parts = [
        f"{p['first_name']} {p['last_name']}",
        f"born {p['date_of_birth']}",
        f"phone {p['phone_number']}",
        f"{p['address_line_1']}"
        + (f" {p['address_line_2']}" if p.get("address_line_2") else "")
        + f", {p['city']}, {p['state']} {p['zip_code']}",
    ]
    return "; ".join(parts)


def _field_errors(exc: ValidationError) -> list[dict]:
    out = []
    for err in exc.errors():
        field = ".".join(str(x) for x in err["loc"]) or "input"
        msg = err["msg"].removeprefix("Value error, ")
        out.append({"field": field, "problem": msg})
    return out


# --------------------------------------------------------------------------- #
# Tool handlers
# --------------------------------------------------------------------------- #

def lookup_patient(args: dict, call_id: Optional[str]) -> dict:
    """Duplicate detection: does a record already exist for this phone number?"""
    try:
        phone = normalize_phone(args.get("phone_number", ""))
    except ValueError as exc:
        return {"status": "needs_correction",
                "errors": [{"field": "phone_number", "problem": str(exc)}]}

    existing = repository.find_by_phone(phone)
    if not existing:
        return {"status": "not_found",
                "instruction": "No existing record. Continue collecting the remaining fields."}
    return {
        "status": "found",
        "patient_id": existing["patient_id"],
        "first_name": existing["first_name"],
        "last_name": existing["last_name"],
        "summary": _spoken_summary(existing),
        "instruction": (
            "A record already exists. Tell the caller we already have a record for "
            f"{existing['first_name']} {existing['last_name']} and ask whether they want to "
            "update it instead of creating a new one. If yes, collect the changes and call "
            "update_patient with this patient_id."
        ),
    }


def register_patient(args: dict, call_id: Optional[str]) -> dict:
    try:
        patient = PatientCreate(**args)
    except ValidationError as exc:
        errors = _field_errors(exc)
        log.warning("register.validation_failed call=%s errors=%s", call_id, errors)
        return {
            "status": "needs_correction",
            "errors": errors,
            "instruction": ("Do not save yet. Re-ask the caller only for the listed "
                            "field(s), then call register_patient again with the full set."),
        }

    try:
        row = repository.create_patient(patient.model_dump())
    except Exception:
        log.exception("register.db_write_failed call=%s", call_id)
        return {
            "status": "error",
            "instruction": ("The save failed. Apologize briefly, tell the caller their "
                            "information was not stored, and offer to try once more."),
        }

    if call_id:
        CALL_TO_PATIENT[call_id] = row["patient_id"]
    repository.log_call(call_id, row["patient_id"], {"event": "registered", **row})
    log.info("registration.complete call=%s patient=%s payload=%s",
             call_id, row["patient_id"], row)
    return {
        "status": "success",
        "patient_id": row["patient_id"],
        "first_name": row["first_name"],
        "instruction": (f"Saved. Tell {row['first_name']} they're all set, then offer to book "
                        "a first appointment. If they say yes, call list_appointment_slots."),
    }


def update_patient(args: dict, call_id: Optional[str]) -> dict:
    patient_id = args.pop("patient_id", None)
    if not patient_id:
        return {"status": "needs_correction",
                "errors": [{"field": "patient_id", "problem": "required — call lookup_patient first"}]}
    try:
        changes = PatientUpdate(**args).model_dump(exclude_unset=True)
    except ValidationError as exc:
        return {"status": "needs_correction", "errors": _field_errors(exc)}

    try:
        row = repository.update_patient(patient_id, changes)
    except Exception:
        log.exception("update.db_write_failed call=%s", call_id)
        return {"status": "error",
                "instruction": "The update failed. Apologize and offer to try again."}

    if not row:
        return {"status": "not_found",
                "instruction": "No such record. Offer to register the caller as a new patient."}

    repository.log_call(call_id, patient_id, {"event": "updated", "changes": changes})
    log.info("update.complete call=%s patient=%s fields=%s", call_id, patient_id, list(changes))
    return {"status": "success", "patient_id": patient_id,
            "updated_fields": list(changes),
            "instruction": f"Updated. Confirm to {row['first_name']} that the changes are saved."}


def list_appointment_slots(args: dict, call_id: Optional[str]) -> dict:
    slots = scheduling.available_slots()
    if not slots:
        return {"status": "none_available",
                "instruction": "No openings this week. Offer to have the office call them back."}
    return {
        "status": "ok",
        "slots": slots,
        "instruction": ("Read these options out loud naturally — day and time only, never the "
                        "slot_id. When the caller picks one, call book_appointment with its slot_id."),
    }


def book_appointment(args: dict, call_id: Optional[str]) -> dict:
    patient_id = args.get("patient_id") or CALL_TO_PATIENT.get(call_id or "")
    slot_id = args.get("slot_id")
    if not patient_id:
        return {"status": "error",
                "instruction": "Register the caller first, then book the appointment."}

    try:
        result = scheduling.book_slot(patient_id, slot_id)
    except Exception:
        log.exception("booking.failed call=%s", call_id)
        return {"status": "error",
                "instruction": ("The booking didn't go through, but the caller's registration "
                                "IS saved. Say the office will call to schedule.")}

    if result["status"] == "invalid_slot":
        return {"status": "needs_correction",
                "instruction": "That wasn't a slot we offered. Re-read the options and ask again."}
    if result["status"] == "unavailable":
        return {"status": "unavailable",
                "instruction": "That slot was just taken. Call list_appointment_slots again."}

    log.info("appointment.booked call=%s patient=%s slot=%s", call_id, patient_id, slot_id)
    return {"status": "booked", "when": result["when"], "provider": result["provider"],
            "instruction": (f"Confirm the appointment with {result['provider']} on "
                            f"{result['when']}, then end the call warmly.")}


HANDLERS = {
    "lookup_patient": lookup_patient,
    "register_patient": register_patient,
    "update_patient": update_patient,
    "list_appointment_slots": list_appointment_slots,
    "book_appointment": book_appointment,
}


# --------------------------------------------------------------------------- #
# Webhook entrypoint
# --------------------------------------------------------------------------- #

def _extract_tool_calls(message: dict) -> list[dict]:
    """Vapi has shipped a few payload shapes; accept all of them."""
    calls = message.get("toolCallList") or message.get("toolCalls") or []
    normalized = []
    for call in calls:
        fn = call.get("function") or {}
        normalized.append({
            "id": call.get("id") or call.get("toolCallId"),
            "name": call.get("name") or fn.get("name"),
            "arguments": call.get("arguments") or fn.get("arguments") or {},
        })
    if not normalized and message.get("functionCall"):  # legacy single-function shape
        fc = message["functionCall"]
        normalized.append({"id": fc.get("id"), "name": fc.get("name"),
                           "arguments": fc.get("parameters") or fc.get("arguments") or {}})
    return normalized


@router.post("/tools")
async def handle_tool_calls(request: Request,
                            x_vapi_secret: Optional[str] = Header(default=None)):
    _authorize(x_vapi_secret)
    body = await request.json()
    message: dict[str, Any] = body.get("message", body)
    call_id = (message.get("call") or {}).get("id")

    results = []
    for call in _extract_tool_calls(message):
        handler = HANDLERS.get(call["name"])
        if handler is None:
            log.warning("vapi.unknown_tool name=%s", call["name"])
            result: dict = {"status": "error", "instruction": "That action isn't available."}
        else:
            args = call["arguments"]
            if isinstance(args, str):  # some models send a JSON string
                import json
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            try:
                result = handler(dict(args), call_id)
            except Exception:
                log.exception("vapi.handler_crashed tool=%s", call["name"])
                result = {"status": "error",
                          "instruction": "Something went wrong. Apologize and offer to retry."}
        results.append({"toolCallId": call["id"], "name": call["name"], "result": result})

    return {"results": results}


@router.post("/events")
async def handle_events(request: Request,
                        x_vapi_secret: Optional[str] = Header(default=None)):
    """Catch-all server webhook — used for end-of-call transcripts (observability)."""
    _authorize(x_vapi_secret)
    body = await request.json()
    message = body.get("message", {})
    kind = message.get("type")

    if kind == "end-of-call-report":
        call_id = (message.get("call") or {}).get("id")
        payload = {
            "event": "end-of-call",
            "ended_reason": message.get("endedReason"),
            "summary": message.get("summary"),
            "transcript": message.get("transcript"),
            "recording_url": message.get("recordingUrl"),
        }
        # Link the transcript to the record this call created, if any.
        patient_id = CALL_TO_PATIENT.pop(call_id, None) if call_id else None
        repository.log_call(call_id, patient_id, payload)
        log.info("call.ended id=%s patient=%s reason=%s",
                 call_id, patient_id, message.get("endedReason"))

    return {"received": True}
