# Voice AI Patient Registration Agent

A phone-callable voice agent that registers new patients through natural
conversation, persists them to a database, and exposes them over a REST API.

| | |
|---|---|
| **Phone number** | **+1 (518) 629-1790** — call to register a patient |
| **API base URL** | https://voice-ai-agent-patient-registration.onrender.com |
| **Dashboard** | https://voice-ai-agent-patient-registration.onrender.com/ |
| **API docs** | https://voice-ai-agent-patient-registration.onrender.com/docs |

> **When you call, you'll first hear "You have a trial account…" — press any key
> and the agent answers.** That prompt is Twilio's trial-account banner, not part
> of this system; removing it requires upgrading the Twilio account off trial.
>
> Hosted on Render's free tier, kept warm by a scheduled ping so calls don't hit a
> cold start. No credentials are needed to browse the dashboard or read the API.

---

## Architecture

```
   Caller ──PSTN──> Twilio number
                        │
                        ▼
              ┌───────────────────┐
              │       Vapi        │  telephony + turn-taking
              │  Soniox  (STT)    │
              │  Kimi K2 (brain)  │  ← system prompt: vapi/system_prompt.md
              │  Elliot  (TTS)    │
              └─────────┬─────────┘
                        │ HTTPS tool calls  (POST /vapi/tools)
                        ▼
        ┌───────────────────────────────────┐
        │  FastAPI backend                  │
        │  routers/vapi.py   ── tool layer  │
        │  routers/patients.py ── REST API  │
        │  schemas.py        ── validation  │
        │  repository.py     ── data access │
        └────────────────┬──────────────────┘
                         ▼
                   SQLite (patients.db)
```

**Separation of concerns.** Vapi owns telephony and the speech pipeline. The
system prompt owns conversation policy. The backend owns validation and
persistence — the voice agent is never trusted to validate, because STT mangles
digits and LLMs hallucinate. Both the tool webhook and the public REST API go
through the same `schemas.py` + `repository.py`, so an HTTP client and the phone
agent can never diverge in behaviour.

### Layout

```
app/
  main.py               FastAPI app, error envelope, logging
  db.py                 SQLite connection + schema DDL
  schemas.py            Pydantic models — all validation & voice-input normalization
  repository.py         Patient data access (create/get/list/update/soft-delete/call logs)
  scheduling.py         Mock appointment slots + booking
  seed.py               2 demo patients on first boot
  routers/patients.py   REST endpoints
  routers/vapi.py       Vapi tool webhook + end-of-call event handler
  routers/dashboard.py  Dashboard page + /appointments, /calls
  static/dashboard.html Single-file web UI (no build step)
vapi/
  system_prompt.md      The assistant's system prompt, with design commentary
  tools.json            The 5 tool definitions to register in Vapi
tests/test_api.py       16 integration tests (REST + webhook + booking + dashboard)
render.yaml             Deploy config
```

### Tech stack & why

| Layer | Choice | Rationale |
|---|---|---|
| Telephony/STT/TTS | **Vapi** + Twilio number | Abstracts the real-time audio pipeline, barge-in, and endpointing. Building that from scratch is not what's being assessed. |
| LLM | **Kimi K2 Instruct 0905** on Groq | Groq for latency (620 ms), which matters more on a phone call than anywhere else. Kimi specifically — see *Choosing the model* below; the first two candidates failed in instructive ways. |
| Backend | **FastAPI** | Pydantic validation *is* the data model — one definition drives request parsing, the tool webhook, and OpenAPI docs. |
| DB | **SQLite** | Single file, zero ops, survives restarts, and the write volume of a phone line is nowhere near its limits. `repository.py` is the only module that would change for Postgres. |
| Hosting | **Render** | Free tier, HTTPS out of the box (Vapi requires it). Kept warm by a GitHub Actions cron rather than paying for an always-on instance. |

### Choosing the model

Three models were tried on live calls. The failures were specific enough to be
worth recording, because they're not things a prompt can fix.

**GPT OSS 20B** — too small to hold five tools plus a branching flow. It stacked
two questions into one breath, called `list_appointment_slots` in the middle of
collecting a phone number, and re-asked for the same field repeatedly.

**GPT OSS 120B** — smarter, but it spoke its own reasoning aloud: *"What's your
date of birth? Wait for user, what's your date of birth?"* and *"We'll wait for
the caller's response."* The GPT-OSS family emits separate analysis and final
channels, and the analysis channel was reaching the TTS. Worse, it **hallucinated
field values** — in one call it volunteered a ZIP code the caller never said, and
would have persisted it. An explicit "never invent a value" rule in the prompt did
not stop it. In a patient intake system, silently fabricated data is the most
serious failure mode there is, so this model was disqualified rather than tuned.

**Kimi K2 Instruct 0905** — same intelligence tier, no reasoning channel to leak,
and it stopped inventing values. 620 ms vs 450 ms is imperceptible against a
~1.1 s end-to-end voice loop.

The lesson worth carrying: when an agent misbehaves, separate *prompt* problems
from *model* problems early. Stage directions in the output and invented field
values are model-capability symptoms — more prompt engineering only wastes time.

### Tuning the voice channel

Two settings mattered more than any prompt wording:

- **Smart Endpointing on, `onNumberSeconds` 3.0.** Callers dictate phone numbers
  in chunks — "two zero six… five five five… zero one nine nine" — and each chunk
  arrives as its own turn. At the default the agent treated the first pause as
  end-of-turn and replied over the caller.
- **Never acknowledge a partial number.** An earlier prompt had the agent confirm
  progress ("Got two-oh-six — and the rest?"). That put its audio on top of the
  caller's and the transcriber dropped a digit, producing a 9-digit phone number.
  The prompt now requires silence until the whole number is spoken.

---

## Setup

### Local

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
copy .env.example .env          # cp on macOS/Linux
uvicorn app.main:app --reload
```

Then `http://localhost:8000/docs`, or:

```bash
curl http://localhost:8000/patients
```

Run the tests:

```bash
pytest -q          # 16 integration tests over the REST API, tool webhook,
                   # appointment booking, transcript linking and the dashboard
```

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_PATH` | no | SQLite file path. Default `patients.db`. On Render with a disk: `/var/data/patients.db`. |
| `VAPI_SERVER_SECRET` | recommended | Shared secret. Set the same value as an `X-Vapi-Secret` header on every Vapi tool. Blank disables the check (local dev only). |
| `SEED_DATA` | no | `true` (default) inserts 2 demo patients when the table is empty. |

No API keys live in this repo. The Groq and Twilio credentials live in the Vapi
dashboard; the only secret the backend knows is the webhook shared secret.

### Deploy (Render, free tier)

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → connect the repo. [render.yaml](render.yaml) is
   picked up automatically, or configure manually:
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Set `VAPI_SERVER_SECRET` when prompted (any long random string —
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
4. Verify: `curl https://<your-app>.onrender.com/health` → `{"data":{"status":"ok"},"error":null}`
5. **Keep it warm.** Free services idle out after 15 minutes and cold-start in
   ~40 s — which a caller hears as dead air on the first tool call, mid-sentence.
   A free cron (cron-job.org, UptimeRobot) hitting `/health` every 10 minutes
   removes the problem entirely. This is the single most important deployment
   step for the phone experience.

---

## Vapi configuration

1. **Tools → Create Tool → Custom Tool** (Vapi's name for a server-backed function
   tool). Create five, from [vapi/tools.json](vapi/tools.json): `lookup_patient`,
   `register_patient`, `update_patient`, `list_appointment_slots`, `book_appointment`.
   - Server URL for each: `https://<your-app>.onrender.com/vapi/tools`
   - Custom header: `X-Vapi-Secret: <VAPI_SERVER_SECRET>`
   - Async: off — the agent must wait for the result before speaking.
2. **Assistant → Model** → Groq / **Kimi K2 Instruct 0905**, temperature **0.3**,
   max tokens **500** (the read-back recites a dozen fields in one turn and
   truncates at the 250 default). Paste the prompt from
   [vapi/system_prompt.md](vapi/system_prompt.md) and attach all five tools, plus
   the built-in **Hang Up** tool so the agent can end the call itself.
3. **Assistant → First Message**:
   > "Thanks for calling Northside Family Health, this is Alex. Are you calling to
   > register as a new patient?"

   Keep the greeting *only* here — repeating it in the system prompt makes the
   agent greet twice.
4. **Assistant → Advanced → Server URL**: `https://<your-app>.onrender.com/vapi/events`
   (same secret header) — this stores end-of-call transcripts. In the same panel:
   **Smart Endpointing → Vapi**, **On Number Seconds → 3.0**, silence timeout 60 s.
   These are what make dictated phone numbers and ZIP codes survive the call.
5. **Phone Numbers** → select the imported Twilio number → assign this assistant.
6. Call the number.

---

## REST API

All responses use the envelope `{"data": ..., "error": ...}`.

| Method | Endpoint | Notes |
|---|---|---|
| `GET` | `/patients` | Filters: `?last_name=&date_of_birth=&phone_number=` plus `limit`/`offset`. Filters accept loose formats (`(415) 555-0142`, `03/15/1990`). |
| `GET` | `/patients/{id}` | 404 if missing or soft-deleted. |
| `POST` | `/patients` | 201 with the created record. |
| `PUT` | `/patients/{id}` | Partial update — send only changed fields. |
| `DELETE` | `/patients/{id}` | Soft delete: sets `deleted_at`, row is retained. |
| `GET` | `/patients/{id}/calls` | Call transcripts and registration payloads for this patient. |
| `GET` | `/patients/{id}/appointments` | Appointments booked for this patient. |
| `GET` | `/appointments` | All bookings, joined to patient names. |
| `GET` | `/calls` | Recent call logs. |
| `GET` | `/` | Dashboard (HTML). |
| `GET` | `/health` | Liveness. |

Status codes: `200`, `201`, `400` (bad query param), `404`, `422` (field
validation, with a per-field `details` array), `500`.

```bash
curl -X POST https://<your-app>.onrender.com/patients \
  -H "Content-Type: application/json" \
  -d '{"first_name":"Jane","last_name":"Davis","date_of_birth":"03/15/1990",
       "sex":"female","phone_number":"(415) 555-0142",
       "address_line_1":"12 Market St","city":"San Francisco",
       "state":"California","zip_code":"94110"}'
```

Note the normalization: `"female"` → `Female`, `"California"` → `CA`,
`"(415) 555-0142"` → `4155550142`, `"03/15/1990"` → `1990-03-15`. Callers speak
in prose; the API meets them halfway on *format* while staying strict on
*content*.

### Data model

Exactly the assessment's field list, plus `deleted_at`. `patient_id` is a UUID,
`created_at`/`updated_at` are UTC ISO-8601. `sex` is constrained by a `CHECK`
against the four allowed values; `phone_number`, `last_name` and `date_of_birth`
are indexed for the lookup paths. Two supporting tables: `call_logs` (one row per
registration and per end-of-call report) and `appointments` (FK to `patients`,
with a partial unique index on `slot_id` so a slot can't be double-booked).

---

## Bonus features

| Bonus | Where |
|---|---|
| **Duplicate detection** | `lookup_patient` matches on phone number and hands the agent the existing name + `patient_id`; the prompt offers an update instead of a second record. |
| **Appointment scheduling** | `list_appointment_slots` / `book_appointment`. Slots are derived from the current date (next 5 weekdays x 4 clinic times, minus what's booked) so the demo never runs dry. Mock provider, real persistence. |
| **Call transcript** | Vapi's end-of-call report hits `/vapi/events`; the call→patient mapping recorded at registration links the transcript to the record. Visible per-patient in the dashboard and at `GET /patients/{id}/calls`. |
| **Dashboard** | `GET /` — sortable-by-recency table, last-name filter, click a row for full demographics, appointments, and transcript. One self-contained HTML file, no build step, polls every 15s so a registration appears while you're still on the call. |
| **Automated tests** | 16 integration tests: `pytest -q`. |
| **Multi-language** | Prompt-level only — see limitations. |

---

## Edge cases handled

| Scenario | Behaviour |
|---|---|
| Invalid DOB (future, pre-1900, unparseable) | Tool returns `needs_correction` naming `date_of_birth`; agent re-asks that one field only, keeping everything else. |
| 3-digit phone / bad area code | Same targeted re-prompt. Area/exchange codes starting `0` or `1` are rejected. |
| DB write fails | Handler catches, logs the traceback, returns `status: error` with an instruction to apologize and offer a retry. The caller never gets silence. |
| Caller wants to start over | Prompt instructs the agent to discard state and restart the field collection. |
| Call drops mid-call | Nothing is written until the caller confirms, so a dropped call leaves no partial record. The end-of-call webhook logs the reason. |
| Unknown tool / handler crash | Webhook still returns HTTP 200 with a speakable error — a 5xx would leave the agent mute. |
| Returning caller | `lookup_patient` matches on phone number and hands the agent the existing name + `patient_id` to offer an update. |
| LLM sends `""` / `"n/a"` for skipped optional fields | Coerced to `NULL` before validation. |

## Observability

Structured stdout logs: `patient.created`, `registration.complete` (with the full
payload), `register.validation_failed`, `call.ended`. The `call_logs` table keeps
the same records plus end-of-call transcripts, queryable alongside patients.

## Known limitations & trade-offs

- **SQLite, single instance.** Fine for one phone line; horizontal scaling would
  need Postgres. Only `db.py`/`repository.py` would change.
- **Render's free tier has no persistent disk**, so the SQLite file survives
  restarts but not *redeploys*. Deliberate trade-off: the assessment's persistence
  requirement is "survives restarts / a second call", which this meets, and the
  cost of a paid instance wasn't warranted for a demo. The practical rule is
  don't push to `main` during a demo window. Upgrading is a three-line change to
  `render.yaml` (see the comment there).
- **Free instances idle out after 15 minutes**, and a ~40 s cold start lands as
  dead air mid-call. Mitigated with an external cron pinging `/health` every 10
  minutes rather than by paying for an always-on instance.
- **Twilio trial account** plays a "You have a trial account" prompt before
  connecting; the caller presses any key to continue. Upgrading removes it but
  requires a payment method, which wasn't warranted for a demo line.
- **No auth on the REST API.** The tool webhook is protected by a shared secret,
  but `/patients` is open. Real deployment needs an API key or OAuth.
- **PHI is stored in plaintext.** Deliberately out of scope per the brief; a real
  system needs encryption at rest, audit logging, and a BAA with each vendor.
- **Duplicate detection keys on phone number alone.** Two family members sharing
  a landline collide; production would match on name + DOB + phone.
- **The call→patient map for transcripts is in-memory.** If the server restarts
  between a registration and that call's end-of-call report, the transcript is
  still stored but not linked to the patient. Losing an association costs nothing
  clinically, so it didn't justify a table.
- **Appointments are mock.** Slots are computed, not fetched from a real calendar,
  and there's no reminder/cancellation flow or timezone handling (everything is UTC).
- **The dashboard is unauthenticated and read-only**, like the rest of the API.
- **Spanish support** is prompt-level only. The STT is configured for English, so
  accuracy on a fully Spanish call will be degraded.
- **Dictated digits remain the weakest link.** Phone numbers, ZIP codes and dates
  are spoken in chunks, and any overlap between the agent's audio and the caller's
  can cost a digit. The current settings and prompt make this rare, not impossible.
  The proper fix is DTMF: Vapi supports keypad input, and letting callers *type*
  their phone number would remove the failure class entirely rather than tuning
  around it. That's the first thing I'd add with more time.

## Next steps

1. **DTMF keypad entry for phone numbers and ZIP codes** — the single highest-value
   change, for the reason in the limitations above.
2. Postgres + Alembic migrations.
3. API-key auth and per-IP rate limiting on `/patients`.
4. Real calendar integration behind `scheduling.available_slots()`, plus SMS
   confirmations for booked appointments.
5. Persist the call→patient map so transcripts survive a restart.
6. Address verification against a USPS/Smarty lookup instead of regex-only ZIP checks.
7. Retry-with-backoff around the DB write before surfacing an error to the caller.
