"""Read-only web UI at `/`. Serves one static file that talks to the same
public REST API — no template engine, no build step, no separate origin."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import repository, scheduling

router = APIRouter(tags=["dashboard"])
_INDEX = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return HTMLResponse(_INDEX.read_text(encoding="utf-8"))


@router.get("/appointments", tags=["appointments"])
def list_all_appointments():
    return {"data": scheduling.list_appointments(), "error": None}


@router.get("/calls", tags=["calls"])
def list_all_calls(limit: int = 50):
    return {"data": repository.list_call_logs(limit=limit), "error": None}
