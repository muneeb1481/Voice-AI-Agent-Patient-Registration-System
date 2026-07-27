"""Two demo records, inserted only when the patients table is empty."""

import logging

from app import repository

log = logging.getLogger("seed")

SEED_PATIENTS = [
    {
        "first_name": "Jane", "last_name": "Doe", "date_of_birth": "1985-04-12",
        "sex": "Female", "phone_number": "4155550142", "email": "jane.doe@example.com",
        "address_line_1": "742 Evergreen Terrace", "address_line_2": "Apt 3B",
        "city": "San Francisco", "state": "CA", "zip_code": "94110",
        "insurance_provider": "Blue Shield", "insurance_member_id": "BS12345678",
        "preferred_language": "English",
        "emergency_contact_name": "John Doe", "emergency_contact_phone": "4155550188",
    },
    {
        "first_name": "Miguel", "last_name": "Alvarez", "date_of_birth": "1972-11-03",
        "sex": "Male", "phone_number": "2125550175", "email": None,
        "address_line_1": "88 Lexington Ave", "address_line_2": None,
        "city": "New York", "state": "NY", "zip_code": "10016",
        "insurance_provider": None, "insurance_member_id": None,
        "preferred_language": "Spanish",
        "emergency_contact_name": None, "emergency_contact_phone": None,
    },
]


def seed_if_empty() -> None:
    if repository.list_patients(limit=1):
        return
    for patient in SEED_PATIENTS:
        repository.create_patient(dict(patient))
    log.info("seeded %d demo patients", len(SEED_PATIENTS))
