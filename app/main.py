"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import init_db
from app.routers import dashboard, patients, vapi
from app.seed import seed_if_empty

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if os.getenv("SEED_DATA", "true").lower() == "true":
        seed_if_empty()
    log.info("API ready (db=%s)", os.getenv("DATABASE_PATH", "patients.db"))
    yield


app = FastAPI(
    title="Patient Registration API",
    description="Backend for a Vapi-powered voice intake agent.",
    version="1.0.0",
    lifespan=lifespan,
)


# --- Uniform {"data": ..., "error": ...} envelope on every error path --------- #

@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    details = [
        {"field": ".".join(str(x) for x in err["loc"][1:]) or "body",
         "message": err["msg"].removeprefix("Value error, ")}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"data": None, "error": {"code": "validation_error",
                                         "message": "One or more fields are invalid.",
                                         "details": details}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_handler(_: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"data": None, "error": {"code": exc.status_code, "message": exc.detail}},
    )


@app.exception_handler(Exception)
async def unhandled_handler(_: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"data": None, "error": {"code": 500, "message": "Internal server error"}},
    )


@app.get("/health", tags=["meta"])
def health():
    return {"data": {"status": "ok"}, "error": None}


app.include_router(dashboard.router)
app.include_router(patients.router)
app.include_router(vapi.router)
