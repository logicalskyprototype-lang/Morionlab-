"""MotionLab job API: authenticated leasing and idempotent result acceptance."""

from __future__ import annotations

import math
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


DEFAULT_LEASE_SECONDS = 60
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "motionlab_jobs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    command: Mapped[str] = mapped_column(String(64), nullable=False)
    args_json: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkerLease(Base):
    __tablename__ = "motionlab_worker_leases"

    installation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    active_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    authorization: dict[str, Any]

    @field_validator("command")
    @classmethod
    def only_allowlisted_command(cls, value: str) -> str:
        if value != "create_cube":
            raise ValueError("Unsupported command.")
        return value

    @field_validator("authorization")
    @classmethod
    def require_approval_and_scope(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("approved") is not True:
            raise ValueError("Explicit approval is required.")
        scopes = value.get("scopes")
        if not isinstance(scopes, list) or "scene.write" not in scopes:
            raise ValueError("The scene.write scope is required.")
        return value

    @field_validator("args")
    @classmethod
    def validate_cube_args(cls, value: dict[str, Any]) -> dict[str, Any]:
        if set(value) - {"name", "size", "location"}:
            raise ValueError("Unsupported create_cube argument.")
        name = value.get("name", "MotionLab Cube")
        size = value.get("size", 2.0)
        location = value.get("location", [0.0, 0.0, 0.0])
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            raise ValueError("Object name must contain 1-64 characters.")
        if isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size) or not 0.001 <= size <= 1000:
            raise ValueError("Cube size is outside the permitted range.")
        if not isinstance(location, (list, tuple)) or len(location) != 3:
            raise ValueError("Location must contain exactly three coordinates.")
        for axis in location:
            if isinstance(axis, bool) or not isinstance(axis, (int, float)) or not math.isfinite(axis) or abs(axis) > 100000:
                raise ValueError("Location contains an invalid coordinate.")
        return {"name": name.strip(), "size": float(size), "location": [float(x) for x in location]}


class ResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: str
    output: dict[str, Any] | None = None
    error: str | None = None

    @field_validator("status")
    @classmethod
    def valid_result_status(cls, value: str) -> str:
        if value not in {"completed", "failed"}:
            raise ValueError("Result status must be completed or failed.")
        return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _token_matches(provided: str | None, expected: str | None) -> bool:
    return bool(provided and expected and hmac.compare_digest(provided, expected))


def _is_future(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return False
    # SQLite drops timezone metadata for DateTime columns; all stored values
    # are UTC, so restore UTC before comparing with the aware clock.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value > now


def create_app(
    database_url: str | None = None,
    addon_token: str | None = None,
    admin_token: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> FastAPI:
    database_url = database_url or os.getenv("DATABASE_URL", "sqlite:///./motionlab.db")
    addon_token = addon_token if addon_token is not None else os.getenv("MOTIONLAB_ADDON_TOKEN")
    admin_token = admin_token if admin_token is not None else os.getenv("MOTIONLAB_ADMIN_TOKEN")
    if lease_seconds < 5 or lease_seconds > 3600:
        raise ValueError("Lease duration must be between 5 and 3600 seconds.")

    engine_options = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        engine_options["connect_args"] = {"check_same_thread": False}
    engine = create_engine(database_url, **engine_options)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with SessionLocal() as bootstrap_session:
        if bootstrap_session.get(WorkerLease, "default-installation") is None:
            bootstrap_session.add(WorkerLease(installation_id="default-installation"))
            bootstrap_session.commit()

    app = FastAPI(title="MotionLab Job API", version="0.1.0")

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request, _exc):
        # Do not echo invalid request bodies: Python's JSON decoder accepts
        # NaN/Infinity, which are not valid JSON numbers and cannot be emitted
        # safely by Starlette's default validation error serializer.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid request payload."},
        )

    def get_session():
        with SessionLocal() as session:
            yield session

    def require_addon(authorization: str | None = Header(default=None)):
        supplied = authorization.removeprefix("Bearer ").strip() if authorization else None
        if not _token_matches(supplied, addon_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid add-on credentials.")
        return "default-installation"

    def require_admin(authorization: str | None = Header(default=None)):
        supplied = authorization.removeprefix("Bearer ").strip() if authorization else None
        if not _token_matches(supplied, admin_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid administrative credentials.")
        return True

    @app.get("/healthz")
    def health():
        return {"status": "ok", "service": "motionlab"}

    @app.post("/v1/admin/jobs", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
    def create_job(payload: JobRequest, session: Session = Depends(get_session)):
        job_id = "ml_" + uuid.uuid4().hex
        job = Job(
            id=job_id,
            command=payload.command,
            args_json=json.dumps(payload.args, separators=(",", ":"), sort_keys=True),
            authorization_json=json.dumps(payload.authorization, separators=(",", ":"), sort_keys=True),
            status="queued",
            attempts=0,
            created_at=_utcnow(),
        )
        session.add(job)
        session.commit()
        return {"id": job_id, "status": "queued"}

    @app.get("/v1/jobs/next")
    def next_job(_installation: str = Depends(require_addon), session: Session = Depends(get_session)):
        now = _utcnow()
        # Serialize claims for this configured installation. PostgreSQL honors
        # this row lock, preventing two poll requests from leasing two jobs.
        lease = session.scalar(
            select(WorkerLease)
            .where(WorkerLease.installation_id == "default-installation")
            .with_for_update()
        )
        if lease is None:
            raise HTTPException(status_code=503, detail="Worker lease state is unavailable.")
        if lease.active_job_id and _is_future(lease.lease_expires_at, now):
            return {"job": None}

        statement = (
            select(Job)
            .where(
                (Job.status == "queued")
                | ((Job.status == "leased") & (Job.lease_expires_at <= now))
            )
            .order_by(Job.created_at)
            .limit(1)
        )
        if engine.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        job = session.scalar(statement)
        if job is None:
            lease.active_job_id = None
            lease.lease_expires_at = None
            session.commit()
            return {"job": None}

        expires_at = now + timedelta(seconds=lease_seconds)
        job.status = "leased"
        job.lease_expires_at = expires_at
        job.attempts += 1
        lease.active_job_id = job.id
        lease.lease_expires_at = expires_at
        session.commit()
        return {
            "job": {
                "id": job.id,
                "command": job.command,
                "args": json.loads(job.args_json),
                "authorization": json.loads(job.authorization_json),
            }
        }

    @app.post("/v1/jobs/{job_id}/result")
    def submit_result(
        job_id: str,
        payload: ResultRequest,
        _installation: str = Depends(require_addon),
        session: Session = Depends(get_session),
    ):
        if not JOB_ID_PATTERN.fullmatch(job_id) or payload.job_id != job_id:
            raise HTTPException(status_code=400, detail="Result job ID does not match the URL.")
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job.")
        if job.status == "completed":
            return {"accepted": True, "result": json.loads(job.result_json)}
        if job.status != "leased":
            raise HTTPException(status_code=409, detail="Job is not currently leased.")
        lease = session.scalar(
            select(WorkerLease)
            .where(WorkerLease.installation_id == "default-installation")
            .with_for_update()
        )
        if lease is None or lease.active_job_id != job_id:
            raise HTTPException(status_code=409, detail="Job is not leased to this installation.")
        # A late result is accepted only while this installation still owns
        # the lease; a redelivery cannot acknowledge another installation's job.
        result = payload.model_dump()
        job.result_json = json.dumps(result, separators=(",", ":"), sort_keys=True)
        job.status = "completed"
        job.lease_expires_at = None
        lease.active_job_id = None
        lease.lease_expires_at = None
        session.commit()
        return {"accepted": True, "result": result}

    app.state.engine = engine
    app.state.session_factory = SessionLocal
    return app


app = create_app()
