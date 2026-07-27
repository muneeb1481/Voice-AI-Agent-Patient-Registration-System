"""Integration tests over the REST API and the Vapi tool webhook.

Each run uses a throwaway SQLite file, set before app import so app.db picks it up.
"""

import os
import tempfile
import uuid

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["SEED_DATA"] = "false"
os.environ["VAPI_SERVER_SECRET"] = ""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def valid_patient(**overrides):
    base = {
        "first_name": "Jane", "last_name": "Davis", "date_of_birth": "03/15/1990",
        "sex": "female", "phone_number": "(415) 555-0142",
        "address_line_1": "12 Market St", "city": "San Francisco",
        "state": "California", "zip_code": "94110",
    }
    base.update(overrides)
    return base


# --- REST -------------------------------------------------------------------- #

def test_create_normalizes_and_returns_201(client):
    r = client.post("/patients", json=valid_patient())
    assert r.status_code == 201
    data = r.json()["data"]
    assert r.json()["error"] is None
    assert data["phone_number"] == "4155550142"   # punctuation stripped
    assert data["state"] == "CA"                  # state name -> abbreviation
    assert data["sex"] == "Female"                # "female" -> canonical enum
    assert data["date_of_birth"] == "1990-03-15"  # stored ISO
    assert uuid.UUID(data["patient_id"])
    assert data["preferred_language"] == "English"


def test_future_dob_is_rejected(client):
    r = client.post("/patients", json=valid_patient(date_of_birth="01/01/2999"))
    assert r.status_code == 422
    assert "date_of_birth" in str(r.json()["error"]["details"])


def test_short_phone_is_rejected(client):
    r = client.post("/patients", json=valid_patient(phone_number="555"))
    assert r.status_code == 422


def test_bad_state_is_rejected(client):
    r = client.post("/patients", json=valid_patient(state="Atlantis"))
    assert r.status_code == 422


def test_get_by_id_and_404(client):
    created = client.post("/patients", json=valid_patient(phone_number="4155550111")).json()["data"]
    assert client.get(f"/patients/{created['patient_id']}").status_code == 200
    assert client.get(f"/patients/{uuid.uuid4()}").status_code == 404


def test_list_filters(client):
    client.post("/patients", json=valid_patient(last_name="Nakamura", phone_number="4155550122"))
    r = client.get("/patients", params={"last_name": "nakamura"})
    assert r.status_code == 200
    assert all(p["last_name"] == "Nakamura" for p in r.json()["data"])

    r = client.get("/patients", params={"phone_number": "(415) 555-0122"})
    assert len(r.json()["data"]) == 1


def test_partial_update(client):
    created = client.post("/patients", json=valid_patient(phone_number="4155550133")).json()["data"]
    r = client.put(f"/patients/{created['patient_id']}", json={"city": "Oakland"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["city"] == "Oakland"
    assert data["last_name"] == created["last_name"]   # untouched
    assert data["updated_at"] >= created["updated_at"]


def test_soft_delete_hides_but_keeps_row(client):
    created = client.post("/patients", json=valid_patient(phone_number="4155550144")).json()["data"]
    pid = created["patient_id"]
    assert client.delete(f"/patients/{pid}").status_code == 200
    assert client.get(f"/patients/{pid}").status_code == 404
    assert client.delete(f"/patients/{pid}").status_code == 404  # already deleted


# --- Vapi webhook ------------------------------------------------------------ #

def tool_call(name, args):
    return {"message": {"type": "tool-calls", "call": {"id": "call_test"},
                        "toolCallList": [{"id": "tc_1", "name": name, "arguments": args}]}}


def test_lookup_not_found_then_found(client):
    phone = "4155550199"
    r = client.post("/vapi/tools", json=tool_call("lookup_patient", {"phone_number": phone}))
    assert r.json()["results"][0]["result"]["status"] == "not_found"

    client.post("/patients", json=valid_patient(phone_number=phone, last_name="Okonkwo"))
    r = client.post("/vapi/tools", json=tool_call("lookup_patient", {"phone_number": f"+1{phone}"}))
    result = r.json()["results"][0]["result"]
    assert result["status"] == "found"
    assert result["last_name"] == "Okonkwo"


def test_register_via_tool_persists(client):
    args = valid_patient(phone_number="4155550166", last_name="Ferraro")
    r = client.post("/vapi/tools", json=tool_call("register_patient", args))
    result = r.json()["results"][0]["result"]
    assert result["status"] == "success"
    assert client.get(f"/patients/{result['patient_id']}").status_code == 200


def test_register_tool_reports_specific_field(client):
    args = valid_patient(date_of_birth="01/01/2999", phone_number="4155550177")
    r = client.post("/vapi/tools", json=tool_call("register_patient", args))
    result = r.json()["results"][0]["result"]
    assert result["status"] == "needs_correction"
    assert result["errors"][0]["field"] == "date_of_birth"


def test_appointment_slots_and_booking(client):
    created = client.post("/patients", json=valid_patient(phone_number="4155550188")).json()["data"]
    pid = created["patient_id"]

    r = client.post("/vapi/tools", json=tool_call("list_appointment_slots", {}))
    slots = r.json()["results"][0]["result"]["slots"]
    assert len(slots) == 3
    assert all("when" in s and "slot_id" in s for s in slots)

    slot_id = slots[0]["slot_id"]
    r = client.post("/vapi/tools",
                    json=tool_call("book_appointment", {"slot_id": slot_id, "patient_id": pid}))
    assert r.json()["results"][0]["result"]["status"] == "booked"

    # Booked slots disappear from availability and can't be double-booked.
    r = client.post("/vapi/tools", json=tool_call("list_appointment_slots", {}))
    assert slot_id not in [s["slot_id"] for s in r.json()["results"][0]["result"]["slots"]]

    r = client.post("/vapi/tools",
                    json=tool_call("book_appointment", {"slot_id": slot_id, "patient_id": pid}))
    assert r.json()["results"][0]["result"]["status"] == "unavailable"

    appts = client.get(f"/patients/{pid}/appointments").json()["data"]
    assert len(appts) == 1 and appts[0]["slot_id"] == slot_id


def test_booking_rejects_unoffered_slot(client):
    created = client.post("/patients", json=valid_patient(phone_number="4155550211")).json()["data"]
    pid = created["patient_id"]
    r = client.post("/vapi/tools",
                    json=tool_call("book_appointment", {"slot_id": "not-a-slot", "patient_id": pid}))
    assert r.json()["results"][0]["result"]["status"] == "needs_correction"


def test_transcript_links_to_patient(client):
    """register_patient remembers the call id so the end-of-call report attaches."""
    args = valid_patient(phone_number="4155550155", last_name="Bergstrom")
    reg = client.post("/vapi/tools", json=tool_call("register_patient", args))
    pid = reg.json()["results"][0]["result"]["patient_id"]

    client.post("/vapi/events", json={"message": {
        "type": "end-of-call-report", "call": {"id": "call_test"},
        "endedReason": "customer-ended-call", "summary": "Registered a new patient.",
        "transcript": "AI: Thanks for calling...\nUser: Hi, I'd like to register.",
    }})

    calls = client.get(f"/patients/{pid}/calls").json()["data"]
    assert any(c["payload"].get("transcript") for c in calls)


def test_dashboard_serves(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Patient Intake" in r.text


def test_tool_errors_never_500(client):
    """A crash inside a handler must still return 200 so the agent can speak."""
    r = client.post("/vapi/tools", json=tool_call("nonexistent_tool", {}))
    assert r.status_code == 200
    assert r.json()["results"][0]["result"]["status"] == "error"
