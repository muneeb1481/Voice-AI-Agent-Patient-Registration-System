"""Pydantic models = the single source of truth for validation.

The voice agent is *not* trusted to validate: speech-to-text mangles digits and
LLMs hallucinate, so everything gets re-validated here. Normalizers are
deliberately forgiving about *format* (a caller says "four one five..." and the
LLM may send "(415) 555-0142") but strict about *content* (must be 10 digits).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

SEX_VALUES = ("Male", "Female", "Other", "Decline to Answer")

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR",
}

# Callers say state names, not abbreviations.
STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "washington dc": "DC", "puerto rico": "PR",
}

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z\-'\. ]{0,49}$")
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
MEMBER_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,50}$")


def normalize_phone(value: Any) -> str:
    """US 10-digit. Accepts +1, punctuation, spoken-out spacing."""
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("must be a valid 10-digit U.S. phone number")
    if digits[0] in "01" or digits[3] in "01":
        raise ValueError("not a valid U.S. phone number (bad area or exchange code)")
    return digits


def normalize_date(value: Any) -> date:
    """Accept MM/DD/YYYY, YYYY-MM-DD, M-D-YYYY, and 'March 5 1990'."""
    if isinstance(value, date):
        parsed = value
    else:
        raw = str(value).strip().replace(",", "")
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%B %d %Y", "%b %d %Y"):
            try:
                parsed = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError("must be a valid date in MM/DD/YYYY format")
    if parsed > date.today():
        raise ValueError("date of birth cannot be in the future")
    if parsed.year < 1900:
        raise ValueError("date of birth is unrealistically old")
    return parsed


def normalize_state(value: Any) -> str:
    raw = str(value).strip()
    if raw.lower() in STATE_NAMES:
        return STATE_NAMES[raw.lower()]
    abbr = raw.upper().replace(".", "").replace(" ", "")
    if abbr in US_STATES:
        return abbr
    raise ValueError("must be a valid 2-letter U.S. state abbreviation")


def normalize_sex(value: Any) -> str:
    raw = str(value).strip().lower()
    mapping = {
        "m": "Male", "male": "Male", "man": "Male",
        "f": "Female", "female": "Female", "woman": "Female",
        "other": "Other", "non-binary": "Other", "nonbinary": "Other",
        "decline": "Decline to Answer", "decline to answer": "Decline to Answer",
        "prefer not to say": "Decline to Answer", "n/a": "Decline to Answer",
    }
    if raw not in mapping:
        raise ValueError(f"must be one of {', '.join(SEX_VALUES)}")
    return mapping[raw]


def _clean_name(value: str, field: str) -> str:
    cleaned = " ".join(str(value).strip().split())
    if not NAME_RE.match(cleaned):
        raise ValueError(f"{field} must be 1-50 alphabetic characters (hyphens/apostrophes allowed)")
    return cleaned


class PatientBase(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date
    sex: Literal["Male", "Female", "Other", "Decline to Answer"]
    phone_number: str
    email: Optional[EmailStr] = None
    address_line_1: str = Field(min_length=1, max_length=200)
    address_line_2: Optional[str] = Field(default=None, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    state: str
    zip_code: str
    insurance_provider: Optional[str] = Field(default=None, max_length=100)
    insurance_member_id: Optional[str] = None
    preferred_language: str = "English"
    emergency_contact_name: Optional[str] = Field(default=None, max_length=100)
    emergency_contact_phone: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _blank_to_none(cls, data: Any) -> Any:
        """LLMs love sending "", "n/a" or "none" for fields the caller skipped."""
        if isinstance(data, dict):
            skip = {"", "n/a", "na", "none", "null", "unknown", "not provided"}
            return {k: (None if isinstance(v, str) and v.strip().lower() in skip else v)
                    for k, v in data.items()}
        return data

    @field_validator("first_name")
    @classmethod
    def _v_first(cls, v: str) -> str:
        return _clean_name(v, "first_name")

    @field_validator("last_name")
    @classmethod
    def _v_last(cls, v: str) -> str:
        return _clean_name(v, "last_name")

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _v_dob(cls, v: Any) -> date:
        return normalize_date(v)

    @field_validator("sex", mode="before")
    @classmethod
    def _v_sex(cls, v: Any) -> str:
        return normalize_sex(v)

    @field_validator("phone_number", "emergency_contact_phone", mode="before")
    @classmethod
    def _v_phone(cls, v: Any) -> Optional[str]:
        return None if v is None else normalize_phone(v)

    @field_validator("state", mode="before")
    @classmethod
    def _v_state(cls, v: Any) -> str:
        return normalize_state(v)

    @field_validator("zip_code", mode="before")
    @classmethod
    def _v_zip(cls, v: Any) -> str:
        raw = re.sub(r"[^\d-]", "", str(v))
        if len(raw) == 9 and raw.isdigit():  # "941105678" -> ZIP+4
            raw = f"{raw[:5]}-{raw[5:]}"
        if not ZIP_RE.match(raw):
            raise ValueError("must be a 5-digit or ZIP+4 U.S. postal code")
        return raw

    @field_validator("insurance_member_id", mode="before")
    @classmethod
    def _v_member_id(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        raw = str(v).strip().replace(" ", "").upper()
        if not MEMBER_ID_RE.match(raw):
            raise ValueError("must be alphanumeric")
        return raw

    @field_validator("address_line_1", "address_line_2", "city",
                     "insurance_provider", "preferred_language",
                     "emergency_contact_name", mode="before")
    @classmethod
    def _v_squash_whitespace(cls, v: Any) -> Any:
        return " ".join(str(v).split()) if isinstance(v, str) else v


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    """Every field optional — PUT supports partial updates."""

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    sex: Optional[Literal["Male", "Female", "Other", "Decline to Answer"]] = None
    phone_number: Optional[str] = None
    email: Optional[EmailStr] = None
    address_line_1: Optional[str] = Field(default=None, max_length=200)
    address_line_2: Optional[str] = Field(default=None, max_length=100)
    city: Optional[str] = Field(default=None, max_length=100)
    state: Optional[str] = None
    zip_code: Optional[str] = None
    insurance_provider: Optional[str] = Field(default=None, max_length=100)
    insurance_member_id: Optional[str] = None
    preferred_language: Optional[str] = None
    emergency_contact_name: Optional[str] = Field(default=None, max_length=100)
    emergency_contact_phone: Optional[str] = None

    # Reuse the create-path normalizers so PUT and POST behave identically.
    @field_validator("first_name")
    @classmethod
    def _v_first(cls, v: str) -> str:
        return _clean_name(v, "first_name")

    @field_validator("last_name")
    @classmethod
    def _v_last(cls, v: str) -> str:
        return _clean_name(v, "last_name")

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _v_dob(cls, v: Any) -> Optional[date]:
        return None if v is None else normalize_date(v)

    @field_validator("sex", mode="before")
    @classmethod
    def _v_sex(cls, v: Any) -> Optional[str]:
        return None if v is None else normalize_sex(v)

    @field_validator("state", mode="before")
    @classmethod
    def _v_state(cls, v: Any) -> Optional[str]:
        return None if v is None else normalize_state(v)

    @field_validator("phone_number", "emergency_contact_phone", mode="before")
    @classmethod
    def _v_phone(cls, v: Any) -> Optional[str]:
        return None if v is None else normalize_phone(v)

    @field_validator("zip_code", mode="before")
    @classmethod
    def _v_zip(cls, v: Any) -> Optional[str]:
        return None if v is None else PatientBase._v_zip(v)


class PatientOut(PatientBase):
    patient_id: str
    created_at: str
    updated_at: str
    deleted_at: Optional[str] = None
