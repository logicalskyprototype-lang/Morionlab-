"""MotionLab job API with installation-scoped credentials and fenced leases."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
from pathlib import Path
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, case, create_engine, delete, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


DEFAULT_LEASE_SECONDS = 60
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
INSTALLATION_ID_PATTERN = re.compile(r"^ins_[a-f0-9]{32}$")


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    __tablename__ = "motionlab_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)




class Project(Base):
    __tablename__ = "motionlab_projects"
    __table_args__ = (
        Index("ix_motionlab_projects_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("motionlab_workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_type: Mapped[str] = mapped_column(String(24), nullable=False, default="3d_scene")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceCredential(Base):
    __tablename__ = "motionlab_workspace_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("motionlab_workspaces.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    __tablename__ = "motionlab_jobs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("motionlab_workspaces.id"), nullable=False, default="wsp_legacy", index=True)
    command: Mapped[str] = mapped_column(String(64), nullable=False)
    args_json: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Installation(Base):
    __tablename__ = "motionlab_installations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("motionlab_workspaces.id"), nullable=False, default="wsp_legacy", index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobAssignment(Base):
    __tablename__ = "motionlab_job_assignments"

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)


class WorkerLease(Base):
    __tablename__ = "motionlab_worker_leases"

    installation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    active_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobLeaseGrant(Base):
    """Stores only a hash of the current fencing token for each leased job."""

    __tablename__ = "motionlab_job_lease_grants"

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    installation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEvent(Base):
    __tablename__ = "motionlab_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("motionlab_workspaces.id"), nullable=True, index=True)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class RateLimitBucket(Base):
    __tablename__ = "motionlab_rate_limit_buckets"

    bucket_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)


class UserAccount(Base):
    __tablename__ = "motionlab_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkspaceMembership(Base):
    __tablename__ = "motionlab_workspace_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_motionlab_membership_user_workspace"),
        Index("ix_motionlab_workspace_memberships_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("motionlab_users.id"), nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("motionlab_workspaces.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BrowserSession(Base):
    __tablename__ = "motionlab_browser_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("motionlab_users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MagicLinkChallenge(Base):
    __tablename__ = "motionlab_magic_link_challenges"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthRateLimitBucket(Base):
    __tablename__ = "motionlab_auth_rate_limit_buckets"

    bucket_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)


def _record_audit(
    session: Session,
    *,
    event_type: str,
    actor_type: str,
    actor_id: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
    workspace_id: str | None = None,
) -> None:
    session.add(
        AuditEvent(
            id=str(uuid.uuid4()),
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            workspace_id=workspace_id,
            details_json=json.dumps(details or {}, separators=(",", ":"), sort_keys=True),
            created_at=_utcnow(),
        )
    )


class WorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 100:
            raise ValueError("Workspace name must contain 1-100 characters.")
        return value




class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = Field(default=None, max_length=2000)
    project_type: str = "3d_scene"

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 100:
            raise ValueError("Project name must contain 1-100 characters.")
        return value

    @field_validator("project_type")
    @classmethod
    def valid_project_type(cls, value: str) -> str:
        if value not in {"3d_scene", "experiment", "animation"}:
            raise ValueError("Unsupported project type.")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = Field(default=None, max_length=2000)
    status: str | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("Project name cannot be null.")
        value = value.strip()
        if not value or len(value) > 100:
            raise ValueError("Project name must contain 1-100 characters.")
        return value

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str | None) -> str | None:
        if value is None or value not in {"active", "archived"}:
            raise ValueError("Project status must be active or archived.")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    def validate_patch(self) -> None:
        if not self.model_fields_set:
            raise ValueError("At least one project field must be supplied.")


class WorkspaceInstallationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 100:
            raise ValueError("Installation name must contain 1-100 characters.")
        return value


class InstallationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    workspace_id: str | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 100:
            raise ValueError("Installation name must contain 1-100 characters.")
        return value


class JobAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: str

    @field_validator("installation_id")
    @classmethod
    def valid_installation_id(cls, value: str) -> str:
        if not INSTALLATION_ID_PATTERN.fullmatch(value):
            raise ValueError("Invalid installation ID.")
        return value


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: str
    command: str
    args: dict[str, Any] = Field(default_factory=dict)
    authorization: dict[str, Any]

    @field_validator("installation_id")
    @classmethod
    def valid_installation_id(cls, value: str) -> str:
        if not INSTALLATION_ID_PATTERN.fullmatch(value):
            raise ValueError("Invalid installation ID.")
        return value

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
        if not _is_finite_number(size) or not 0.001 <= size <= 1000:
            raise ValueError("Cube size is outside the permitted range.")
        if not isinstance(location, (list, tuple)) or len(location) != 3:
            raise ValueError("Location must contain exactly three coordinates.")
        for axis in location:
            if not _is_finite_number(axis) or abs(axis) > 100000:
                raise ValueError("Location contains an invalid coordinate.")
        return {"name": name.strip(), "size": float(size), "location": [float(x) for x in location]}


class ResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    lease_token: str
    status: str
    output: dict[str, Any] | None = None
    error: str | None = None

    @field_validator("lease_token")
    @classmethod
    def valid_lease_token(cls, value: str) -> str:
        if not value or len(value) > 128:
            raise ValueError("A valid lease token is required.")
        return value

    @field_validator("status")
    @classmethod
    def valid_result_status(cls, value: str) -> str:
        if value not in {"completed", "failed"}:
            raise ValueError("Result status must be completed or failed.")
        return value

    @field_validator("output")
    @classmethod
    def output_must_be_strict_json(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            try:
                serialized = json.dumps(value, allow_nan=False, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                raise ValueError("Result output must contain finite JSON values.") from exc
            if len(serialized.encode("utf-8")) > 256 * 1024:
                raise ValueError("Result output exceeds the 256 KiB limit.")
        return value

    @field_validator("error")
    @classmethod
    def error_message_is_bounded(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 2048:
            raise ValueError("Result error message exceeds the 2048 character limit.")
        return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


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
    admin_token: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    initialize_schema: bool | None = None,
    rate_limit_requests: int = 120,
    rate_limit_window_seconds: int = 60,
    email_sender=None,
) -> FastAPI:
    database_url = database_url or os.getenv("DATABASE_URL", "sqlite:///./motionlab.db")
    admin_token = admin_token if admin_token is not None else os.getenv("MOTIONLAB_ADMIN_TOKEN")
    production = os.getenv("MOTIONLAB_ENV", "development").lower() == "production"
    if production:
        if not admin_token or len(admin_token) < 32:
            raise RuntimeError("Production requires MOTIONLAB_ADMIN_TOKEN with at least 32 characters.")
        if not database_url.startswith("postgresql+psycopg://"):
            raise RuntimeError("Production requires PostgreSQL via the psycopg SQLAlchemy driver.")
    if initialize_schema is None:
        initialize_schema = not production and os.getenv("MOTIONLAB_SKIP_SCHEMA_INIT") != "1"
    if production and initialize_schema:
        raise RuntimeError("Production schema changes must be applied with Alembic, not create_all().")
    if production:
            configured_origin = os.getenv("MOTIONLAB_APP_ORIGIN", "")
            parsed_origin = urlsplit(configured_origin)
            if (
                parsed_origin.scheme != "https"
                or not parsed_origin.netloc
                or parsed_origin.path not in {"", "/"}
                or parsed_origin.query
                or parsed_origin.fragment
                or parsed_origin.username
                or parsed_origin.password
            ):
                raise RuntimeError(
                    "Production requires MOTIONLAB_APP_ORIGIN to be a valid HTTPS origin."
                )
    if rate_limit_requests < 1 or rate_limit_requests > 10000:
        raise ValueError("Rate limit must be between 1 and 10000 requests.")
    if rate_limit_window_seconds < 1 or rate_limit_window_seconds > 3600:
        raise ValueError("Rate limit window must be between 1 and 3600 seconds.")
    if lease_seconds < 5 or lease_seconds > 3600:
        raise ValueError("Lease duration must be between 5 and 3600 seconds.")

    engine_options = {"pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        engine_options["connect_args"] = {"check_same_thread": False}
    engine = create_engine(database_url, **engine_options)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    if initialize_schema:
        Base.metadata.create_all(engine)

    # Development/test databases created directly from metadata still need the
    # explicit single-tenant adoption workspace used by the Alembic migration.
    with SessionLocal() as bootstrap_session:
        if bootstrap_session.get(Workspace, "wsp_legacy") is None:
            bootstrap_session.add(
                Workspace(
                    id="wsp_legacy",
                    name="Legacy workspace",
                    status="active",
                    created_at=_utcnow(),
                )
            )
            bootstrap_session.commit()

    if production and email_sender is None:
        from backend.email_resend import build_resend_sender

        email_sender = build_resend_sender()

    app = FastAPI(title="MotionLab Job API", version="0.3.0")

    # Serve the browser application from the API origin. This keeps session
    # cookies, CSRF Origin checks, and browser API calls on one origin.
    web_root = Path(__file__).resolve().parent.parent / "web"
    if web_root.is_dir():
        app.mount("/web", StaticFiles(directory=web_root), name="motionlab-web")

        @app.get("/", include_in_schema=False)
        def web_index():
            return FileResponse(web_root / "index.html")

    app.state.rate_limit_cleanup_counter = 0

    @app.middleware("http")
    async def enforce_api_rate_limit(request: Request, call_next):
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)

        supplied = _bearer_token(request.headers.get("authorization"))
        valid_credential = _token_matches(supplied, admin_token)
        if supplied and not valid_credential:
            try:
                with SessionLocal() as auth_session:
                    valid_credential = auth_session.scalar(
                        select(Installation.id).where(
                            Installation.token_hash == _token_hash(supplied),
                            Installation.revoked_at.is_(None),
                        )
                    ) is not None
                    if not valid_credential:
                        valid_credential = auth_session.scalar(
                            select(WorkspaceCredential.id).where(
                                WorkspaceCredential.token_hash == _token_hash(supplied),
                                WorkspaceCredential.revoked_at.is_(None),
                            )
                        ) is not None
            except SQLAlchemyError:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"detail": "API protection is temporarily unavailable."},
                )

        if supplied and valid_credential:
            principal = "credential:" + _token_hash(supplied)
        else:
            # Unknown or invalid credentials share the client-IP bucket,
            # preventing attackers from bypassing limits by rotating tokens.
            # Only request.client.host is trusted; forwarded headers are ignored.
            client_host = request.client.host if request.client else "unknown"
            principal = "ip:" + client_host
        bucket_key = hashlib.sha256(principal.encode("utf-8")).hexdigest()
        now_epoch = int(_utcnow().timestamp())
        window_start = now_epoch - (now_epoch % rate_limit_window_seconds)

        try:
            with SessionLocal() as rate_session:
                insert_fn = (
                    postgresql_insert
                    if engine.dialect.name == "postgresql"
                    else sqlite_insert
                )
                statement = insert_fn(RateLimitBucket).values(
                    bucket_key=bucket_key,
                    window_start_epoch=window_start,
                    request_count=1,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[RateLimitBucket.bucket_key],
                    set_={
                        "window_start_epoch": window_start,
                        "request_count": case(
                            (
                                RateLimitBucket.window_start_epoch == window_start,
                                RateLimitBucket.request_count + 1,
                            ),
                            else_=1,
                        ),
                    },
                ).returning(RateLimitBucket.request_count)
                request_count = rate_session.scalar(statement)
                rate_session.commit()

                app.state.rate_limit_cleanup_counter += 1
                if app.state.rate_limit_cleanup_counter >= 1000:
                    app.state.rate_limit_cleanup_counter = 0
                    rate_session.execute(
                        delete(RateLimitBucket).where(
                            RateLimitBucket.window_start_epoch
                            < now_epoch - (2 * rate_limit_window_seconds)
                        )
                    )
                    rate_session.commit()
        except SQLAlchemyError:
            # Fail closed: do not silently disable abuse controls if their
            # persistence layer is unavailable.
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "API protection is temporarily unavailable."},
            )

        if request_count > rate_limit_requests:
            retry_after = max(1, window_start + rate_limit_window_seconds - now_epoch)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded."},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request, _exc):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid request payload."},
        )

    def get_session():
        with SessionLocal() as session:
            yield session

    def require_admin(authorization: str | None = Header(default=None)):
        supplied = _bearer_token(authorization)
        if not _token_matches(supplied, admin_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid administrative credentials.")
        return True

    def require_addon(
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session),
    ):
        supplied = _bearer_token(authorization)
        if not supplied:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid add-on credentials.")
        installation = session.scalar(
            select(Installation).where(Installation.token_hash == _token_hash(supplied))
        )
        if installation is None or installation.revoked_at is not None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid add-on credentials.")
        workspace = session.get(Workspace, installation.workspace_id)
        if workspace is None or workspace.status != "active":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Installation workspace is not active.")
        return installation.id

    def require_workspace(
        authorization: str | None = Header(default=None),
        session: Session = Depends(get_session),
    ):
        supplied = _bearer_token(authorization)
        if not supplied:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid workspace credentials.")
        credential = session.scalar(
            select(WorkspaceCredential).where(
                WorkspaceCredential.token_hash == _token_hash(supplied)
            )
        )
        if credential is None or credential.revoked_at is not None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid workspace credentials.")
        workspace = session.get(Workspace, credential.workspace_id)
        if workspace is None or workspace.status != "active":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Workspace is not active.")
        return workspace

    from backend.auth import build_auth_router

    app.state.magic_link_outbox = []
    app.include_router(
        build_auth_router(
            session_factory=SessionLocal,
            engine=engine,
            UserAccount=UserAccount,
            WorkspaceMembership=WorkspaceMembership,
            BrowserSession=BrowserSession,
            MagicLinkChallenge=MagicLinkChallenge,
            AuthRateLimitBucket=AuthRateLimitBucket,
            Workspace=Workspace,
            Project=Project,
            AuditEvent=AuditEvent,
            utcnow=_utcnow,
            token_hash=_token_hash,
            record_audit=_record_audit,
            outbox=app.state.magic_link_outbox,
            production=production,
            app_origin=os.getenv("MOTIONLAB_APP_ORIGIN"),
            email_sender=email_sender,
        )
    )

    @app.get("/healthz")
    def health():
        return {"status": "ok", "service": "motionlab"}

    @app.get("/readyz")
    def readiness():
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            raise HTTPException(status_code=503, detail="Database is not ready.")
        return {"status": "ready", "service": "motionlab"}

    @app.post(
        "/v1/admin/workspaces",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
    )
    def create_workspace(payload: WorkspaceRequest, session: Session = Depends(get_session)):
        workspace_id = "wsp_" + uuid.uuid4().hex
        access_token = secrets.token_urlsafe(48)
        workspace = Workspace(id=workspace_id, name=payload.name, status="active", created_at=_utcnow())
        credential = WorkspaceCredential(
            id=str(uuid.uuid4()), workspace_id=workspace_id,
            token_hash=_token_hash(access_token), created_at=_utcnow(),
        )
        session.add_all([workspace, credential])
        session.flush()
        _record_audit(session, event_type="workspace.created", actor_type="admin", actor_id="admin",
            target_type="workspace", target_id=workspace_id, details={"name": payload.name},
            workspace_id=workspace_id)
        session.commit()
        return {"workspace_id": workspace_id, "access_token": access_token}

    @app.post("/v1/admin/workspaces/{workspace_id}/credentials",
        status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
    def issue_workspace_credential(workspace_id: str, session: Session = Depends(get_session)):
        workspace = session.get(Workspace, workspace_id)
        if workspace is None or workspace.status != "active":
            raise HTTPException(status_code=404, detail="Active workspace not found.")
        access_token = secrets.token_urlsafe(48)
        credential = WorkspaceCredential(id=str(uuid.uuid4()), workspace_id=workspace_id,
            token_hash=_token_hash(access_token), created_at=_utcnow())
        session.add(credential)
        _record_audit(session, event_type="workspace.credential_issued", actor_type="admin",
            actor_id="admin", target_type="workspace", target_id=workspace_id,
            workspace_id=workspace_id)
        session.commit()
        return {"credential_id": credential.id, "access_token": access_token}

    @app.post("/v1/admin/workspace-credentials/{credential_id}/revoke",
        dependencies=[Depends(require_admin)])
    def revoke_workspace_credential(credential_id: str, session: Session = Depends(get_session)):
        credential = session.get(WorkspaceCredential, credential_id)
        if credential is None:
            raise HTTPException(status_code=404, detail="Unknown workspace credential.")
        if credential.revoked_at is None:
            credential.revoked_at = _utcnow()
            _record_audit(session, event_type="workspace.credential_revoked", actor_type="admin",
                actor_id="admin", target_type="workspace", target_id=credential.workspace_id,
                workspace_id=credential.workspace_id, details={"credential_id": credential.id})
            session.commit()
        return {"revoked": True}

    @app.post("/v1/admin/workspaces/{workspace_id}/deactivate",
        dependencies=[Depends(require_admin)])
    def deactivate_workspace(workspace_id: str, session: Session = Depends(get_session)):
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise HTTPException(status_code=404, detail="Unknown workspace.")
        if workspace.status != "inactive":
            workspace.status = "inactive"
            _record_audit(session, event_type="workspace.deactivated", actor_type="admin",
                actor_id="admin", target_type="workspace", target_id=workspace_id,
                workspace_id=workspace_id)
            session.commit()
        return {"deactivated": True}

    @app.post(
        "/v1/admin/installations",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
    )
    def create_installation(payload: InstallationRequest, session: Session = Depends(get_session)):
        workspace_id = payload.workspace_id or "wsp_legacy"
        workspace = session.get(Workspace, workspace_id)
        if workspace is None or workspace.status != "active":
            raise HTTPException(status_code=404, detail="Active workspace not found.")
        installation_id = "ins_" + uuid.uuid4().hex
        access_token = secrets.token_urlsafe(32)
        installation = Installation(
            id=installation_id, workspace_id=workspace_id, name=payload.name,
            token_hash=_token_hash(access_token), created_at=_utcnow(),
        )
        session.add(installation)
        session.add(WorkerLease(installation_id=installation_id))
        _record_audit(session, event_type="installation.created", actor_type="admin",
            actor_id="admin", target_type="installation", target_id=installation_id,
            details={"name": payload.name}, workspace_id=workspace_id)
        session.commit()
        return {"installation_id": installation_id, "access_token": access_token}

    @app.post(
        "/v1/admin/installations/{installation_id}/revoke",
        dependencies=[Depends(require_admin)],
    )
    def revoke_installation(installation_id: str, session: Session = Depends(get_session)):
        installation = session.get(Installation, installation_id)
        if installation is None:
            raise HTTPException(status_code=404, detail="Unknown installation.")
        if installation.revoked_at is not None:
            return {"revoked": True}
        installation.revoked_at = _utcnow()
        assignments = session.scalars(
            select(JobAssignment).where(JobAssignment.installation_id == installation_id)
        ).all()
        for assignment in assignments:
            job = session.get(Job, assignment.job_id)
            if job is not None and job.status in {"queued", "leased"}:
                job.status = "queued"
                job.lease_expires_at = None
                grant = session.get(JobLeaseGrant, job.id)
                if grant is not None:
                    session.delete(grant)
                session.delete(assignment)
        lease = session.get(WorkerLease, installation_id)
        if lease is not None:
            lease.active_job_id = None
            lease.lease_expires_at = None
        _record_audit(session, event_type="installation.revoked", actor_type="admin", actor_id="admin", target_type="installation", target_id=installation_id, workspace_id=installation.workspace_id)
        session.commit()
        return {"revoked": True}

    @app.post(
        "/v1/admin/jobs",
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
    )
    def create_job(payload: JobRequest, session: Session = Depends(get_session)):
        installation = session.get(Installation, payload.installation_id)
        if installation is None or installation.revoked_at is not None:
            raise HTTPException(status_code=404, detail="Active target installation not found.")
        workspace = session.get(Workspace, installation.workspace_id)
        if workspace is None or workspace.status != "active":
            raise HTTPException(status_code=403, detail="Target installation workspace is not active.")
        job_id = "ml_" + uuid.uuid4().hex
        job = Job(
            id=job_id,
            workspace_id=installation.workspace_id,
            command=payload.command,
            args_json=json.dumps(payload.args, separators=(",", ":"), sort_keys=True),
            authorization_json=json.dumps(payload.authorization, separators=(",", ":"), sort_keys=True),
            status="queued",
            attempts=0,
            created_at=_utcnow(),
        )
        session.add(job)
        session.add(JobAssignment(job_id=job_id, installation_id=installation.id))
        _record_audit(session, event_type="job.created", actor_type="admin", actor_id="admin", target_type="job", target_id=job_id, details={"installation_id": installation.id, "command": payload.command}, workspace_id=installation.workspace_id)
        session.commit()
        return {"id": job_id, "status": "queued", "installation_id": installation.id}

    @app.post(
        "/v1/admin/jobs/{job_id}/assign",
        dependencies=[Depends(require_admin)],
    )
    def assign_job(job_id: str, payload: JobAssignmentRequest, session: Session = Depends(get_session)):
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job.")
        if job.status != "queued":
            raise HTTPException(status_code=409, detail="Only queued jobs can be assigned.")
        installation = session.get(Installation, payload.installation_id)
        if installation is None or installation.revoked_at is not None:
            raise HTTPException(status_code=404, detail="Active target installation not found.")
        if installation.workspace_id != job.workspace_id:
            raise HTTPException(status_code=409, detail="Job and installation must belong to the same workspace.")
        assignment = session.get(JobAssignment, job_id)
        if assignment is None:
            assignment = JobAssignment(job_id=job_id, installation_id=installation.id)
            session.add(assignment)
        else:
            assignment.installation_id = installation.id
        _record_audit(session, event_type="job.assigned", actor_type="admin", actor_id="admin", target_type="job", target_id=job_id, details={"installation_id": installation.id}, workspace_id=job.workspace_id)
        session.commit()
        return {"id": job_id, "installation_id": installation.id, "status": job.status}

    @app.get(
        "/v1/admin/audit-events",
        dependencies=[Depends(require_admin)],
    )
    def list_audit_events(
        limit: int = Query(default=100, ge=1, le=500),
        session: Session = Depends(get_session),
    ):
        events = session.scalars(
            select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit)
        ).all()
        return {
            "events": [
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "actor_type": event.actor_type,
                    "actor_id": event.actor_id,
                    "target_type": event.target_type,
                    "target_id": event.target_id,
                    "details": json.loads(event.details_json),
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ]
        }

    @app.post("/v1/workspaces/installations", status_code=status.HTTP_201_CREATED)
    def create_workspace_installation(
        payload: WorkspaceInstallationRequest,
        workspace: Workspace = Depends(require_workspace),
        session: Session = Depends(get_session),
    ):
        installation_id = "ins_" + uuid.uuid4().hex
        access_token = secrets.token_urlsafe(32)
        installation = Installation(
            id=installation_id, workspace_id=workspace.id, name=payload.name,
            token_hash=_token_hash(access_token), created_at=_utcnow(),
        )
        session.add(installation)
        session.add(WorkerLease(installation_id=installation_id))
        _record_audit(
            session, event_type="installation.created", actor_type="workspace",
            actor_id=workspace.id, target_type="installation", target_id=installation_id,
            details={"name": payload.name}, workspace_id=workspace.id,
        )
        session.commit()
        return {"installation_id": installation_id, "access_token": access_token}

    @app.get("/v1/workspaces/installations")
    def list_workspace_installations(
        workspace: Workspace = Depends(require_workspace),
        session: Session = Depends(get_session),
    ):
        installations = session.scalars(
            select(Installation).where(Installation.workspace_id == workspace.id)
            .order_by(Installation.created_at.desc(), Installation.id.desc())
        ).all()
        return {"installations": [
            {"installation_id": item.id, "name": item.name,
             "created_at": item.created_at.isoformat(),
             "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None}
            for item in installations
        ]}

    @app.post("/v1/workspaces/installations/{installation_id}/revoke")
    def revoke_workspace_installation(
        installation_id: str,
        workspace: Workspace = Depends(require_workspace),
        session: Session = Depends(get_session),
    ):
        installation = session.get(Installation, installation_id)
        if installation is None or installation.workspace_id != workspace.id:
            raise HTTPException(status_code=404, detail="Unknown installation.")
        if installation.revoked_at is not None:
            return {"revoked": True}
        installation.revoked_at = _utcnow()
        assignments = session.scalars(
            select(JobAssignment).where(JobAssignment.installation_id == installation_id)
        ).all()
        for assignment in assignments:
            job = session.get(Job, assignment.job_id)
            if job is not None and job.workspace_id == workspace.id and job.status in {"queued", "leased"}:
                job.status = "queued"
                job.lease_expires_at = None
                grant = session.get(JobLeaseGrant, job.id)
                if grant is not None:
                    session.delete(grant)
                session.delete(assignment)
        lease = session.get(WorkerLease, installation_id)
        if lease is not None:
            lease.active_job_id = None
            lease.lease_expires_at = None
        _record_audit(
            session, event_type="installation.revoked", actor_type="workspace",
            actor_id=workspace.id, target_type="installation", target_id=installation_id,
            workspace_id=workspace.id,
        )
        session.commit()
        return {"revoked": True}

    @app.post("/v1/workspaces/jobs", status_code=status.HTTP_201_CREATED)
    def enqueue_workspace_job(
        payload: JobRequest,
        workspace: Workspace = Depends(require_workspace),
        session: Session = Depends(get_session),
    ):
        installation = session.get(Installation, payload.installation_id)
        if installation is None or installation.revoked_at is not None or installation.workspace_id != workspace.id:
            raise HTTPException(status_code=404, detail="Active target installation not found in this workspace.")
        job_id = "ml_" + uuid.uuid4().hex
        job = Job(
            id=job_id, workspace_id=workspace.id, command=payload.command,
            args_json=json.dumps(payload.args, separators=(",", ":"), sort_keys=True),
            authorization_json=json.dumps(payload.authorization, separators=(",", ":"), sort_keys=True),
            status="queued", attempts=0, created_at=_utcnow(),
        )
        session.add(job)
        session.add(JobAssignment(job_id=job_id, installation_id=installation.id))
        _record_audit(session, event_type="job.created", actor_type="workspace",
            actor_id=workspace.id, target_type="job", target_id=job_id,
            details={"installation_id": installation.id, "command": payload.command},
            workspace_id=workspace.id)
        session.commit()
        return {"id": job_id, "status": "queued", "installation_id": installation.id}

    @app.post("/v1/workspaces/projects", status_code=status.HTTP_201_CREATED)
    def create_workspace_project(
        payload: ProjectCreateRequest,
        workspace: Workspace = Depends(require_workspace),
        session: Session = Depends(get_session),
    ):
        now = _utcnow()
        project = Project(
            id="prj_" + uuid.uuid4().hex,
            workspace_id=workspace.id,
            name=payload.name,
            description=payload.description,
            project_type=payload.project_type,
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(project)
        _record_audit(
            session, event_type="project.created", actor_type="workspace",
            actor_id=workspace.id, target_type="project", target_id=project.id,
            details={"name": project.name, "project_type": project.project_type},
            workspace_id=workspace.id,
        )
        session.commit()
        return {
            "project": {
                "id": project.id, "name": project.name,
                "description": project.description, "project_type": project.project_type,
                "status": project.status, "created_at": project.created_at.isoformat(),
                "updated_at": project.updated_at.isoformat(),
            }
        }

    @app.get("/v1/workspaces/projects")
    def list_workspace_projects(
        include_archived: bool = False,
        limit: int = Query(default=50, ge=1, le=100),
        workspace: Workspace = Depends(require_workspace),
        session: Session = Depends(get_session),
    ):
        statement = select(Project).where(Project.workspace_id == workspace.id)
        if not include_archived:
            statement = statement.where(Project.status == "active")
        projects = session.scalars(
            statement.order_by(Project.created_at.desc(), Project.id.desc()).limit(limit)
        ).all()
        return {"projects": [
            {
                "id": item.id, "name": item.name, "description": item.description,
                "project_type": item.project_type, "status": item.status,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }
            for item in projects
        ]}

    @app.get("/v1/workspaces/projects/{project_id}")
    def get_workspace_project(
        project_id: str,
        workspace: Workspace = Depends(require_workspace),
        session: Session = Depends(get_session),
    ):
        project = session.scalar(
            select(Project).where(
                Project.id == project_id,
                Project.workspace_id == workspace.id,
            )
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Unknown project.")
        return {"project": {
            "id": project.id, "name": project.name,
            "description": project.description, "project_type": project.project_type,
            "status": project.status, "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }}

    @app.patch("/v1/workspaces/projects/{project_id}")
    def update_workspace_project(
        project_id: str,
        payload: ProjectUpdateRequest,
        workspace: Workspace = Depends(require_workspace),
        session: Session = Depends(get_session),
    ):
        try:
            payload.validate_patch()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        project = session.scalar(
            select(Project).where(
                Project.id == project_id,
                Project.workspace_id == workspace.id,
            ).with_for_update()
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Unknown project.")
        changes = payload.model_dump(exclude_unset=True)
        if project.status == "archived" and changes.get("status") != "active":
            raise HTTPException(status_code=409, detail="Archived projects must be reactivated before editing.")
        for field, value in changes.items():
            setattr(project, field, value)
        project.updated_at = _utcnow()
        event_type = "project.archived" if project.status == "archived" else "project.updated"
        _record_audit(
            session, event_type=event_type, actor_type="workspace",
            actor_id=workspace.id, target_type="project", target_id=project.id,
            details={"changed_fields": sorted(changes.keys())},
            workspace_id=workspace.id,
        )
        session.commit()
        return {"project": {
            "id": project.id, "name": project.name,
            "description": project.description, "project_type": project.project_type,
            "status": project.status, "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }}

    @app.get("/v1/workspaces/audit-events")
    def list_workspace_audit_events(
        limit: int = Query(default=100, ge=1, le=500),
        workspace: Workspace = Depends(require_workspace),
        session: Session = Depends(get_session),
    ):
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.workspace_id == workspace.id)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit)
        ).all()
        return {"events": [
            {"id": event.id, "event_type": event.event_type, "actor_type": event.actor_type,
             "actor_id": event.actor_id, "target_type": event.target_type, "target_id": event.target_id,
             "details": json.loads(event.details_json), "created_at": event.created_at.isoformat()}
            for event in events
        ]}

    @app.get("/v1/jobs/next")
    def next_job(_installation: str = Depends(require_addon), session: Session = Depends(get_session)):
        now = _utcnow()
        lease = session.scalar(
            select(WorkerLease)
            .where(WorkerLease.installation_id == _installation)
            .with_for_update()
        )
        if lease is None:
            raise HTTPException(status_code=503, detail="Worker lease state is unavailable.")
        if lease.active_job_id and _is_future(lease.lease_expires_at, now):
            return {"job": None}

        installation = session.get(Installation, _installation)
        if installation is None:
            raise HTTPException(status_code=401, detail="Invalid add-on credentials.")
        assigned_job_ids = select(JobAssignment.job_id).where(
            JobAssignment.installation_id == _installation
        )
        statement = (
            select(Job)
            .where(
                Job.workspace_id == installation.workspace_id,
                Job.id.in_(assigned_job_ids),
                (Job.status == "queued")
                | ((Job.status == "leased") & (Job.lease_expires_at <= now)),
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
        lease_token = secrets.token_urlsafe(32)
        job.status = "leased"
        job.lease_expires_at = expires_at
        job.attempts += 1
        lease.active_job_id = job.id
        lease.lease_expires_at = expires_at
        grant = session.get(JobLeaseGrant, job.id)
        if grant is None:
            grant = JobLeaseGrant(
                job_id=job.id,
                installation_id=_installation,
                token_hash=_token_hash(lease_token),
                expires_at=expires_at,
            )
            session.add(grant)
        else:
            grant.installation_id = _installation
            grant.token_hash = _token_hash(lease_token)
            grant.expires_at = expires_at
        _record_audit(session, event_type="job.leased", actor_type="installation", actor_id=_installation, target_type="job", target_id=job.id, details={"attempt": job.attempts, "lease_expires_at": expires_at.isoformat()}, workspace_id=job.workspace_id)
        session.commit()
        return {
            "job": {
                "id": job.id,
                "command": job.command,
                "args": json.loads(job.args_json),
                "authorization": json.loads(job.authorization_json),
                "lease_token": lease_token,
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
        # Keep lock order consistent: installation lease, then job.
        lease = session.scalar(
            select(WorkerLease)
            .where(WorkerLease.installation_id == _installation)
            .with_for_update()
        )
        job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        assignment = session.get(JobAssignment, job_id)
        installation = session.get(Installation, _installation)
        if (job is None or assignment is None or assignment.installation_id != _installation
            or installation is None or job.workspace_id != installation.workspace_id):
            raise HTTPException(status_code=404, detail="Unknown job.")
        grant = session.get(JobLeaseGrant, job_id)
        if (
            grant is None
            or grant.installation_id != _installation
            or not hmac.compare_digest(grant.token_hash, _token_hash(payload.lease_token))
        ):
            raise HTTPException(status_code=409, detail="Lease token is no longer valid for this job.")

        # Never persist the bearer lease token in the result record.
        result = payload.model_dump(exclude={"lease_token"})
        if job.status in {"completed", "failed"}:
            original = json.loads(job.result_json)
            if original != result:
                raise HTTPException(status_code=409, detail="A different result was already accepted for this job.")
            return {"accepted": True, "result": original}
        if job.status != "leased":
            raise HTTPException(status_code=409, detail="Job is not currently leased.")
        if (
            lease is None
            or lease.active_job_id != job_id
            or not _is_future(lease.lease_expires_at, _utcnow())
            or not _is_future(grant.expires_at, _utcnow())
        ):
            raise HTTPException(status_code=409, detail="Job lease is no longer active for this installation.")

        job.result_json = json.dumps(result, separators=(",", ":"), sort_keys=True)
        job.status = payload.status
        job.lease_expires_at = None
        lease.active_job_id = None
        lease.lease_expires_at = None
        _record_audit(session, event_type="job.result_accepted", actor_type="installation", actor_id=_installation, target_type="job", target_id=job_id, details={"status": payload.status}, workspace_id=job.workspace_id)
        session.commit()
        return {"accepted": True, "result": result}

    app.state.engine = engine
    app.state.session_factory = SessionLocal
    return app


app = create_app()
