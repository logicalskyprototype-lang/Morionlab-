"""Browser identity and public magic-link authentication for MotionLab.

The email delivery boundary is deliberately provider-neutral. In development,
links are recorded in an in-memory outbox for tests; production requires an
explicit email sender integration before links can be issued.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker


SESSION_COOKIE = "motionlab_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
MAGIC_LINK_SECONDS = 15 * 60
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class MagicLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid email address.")
        return value


class MagicLinkVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=128)


class BrowserProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    project_type: str = "3d_scene"

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project name is required.")
        return value

    @field_validator("project_type")
    @classmethod
    def validate_project_type(cls, value: str) -> str:
        if value not in {"3d_scene", "experiment", "animation"}:
            raise ValueError("Unsupported project type.")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class BrowserProjectUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    status: str | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Project name is required.")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is not None and value not in {"active", "archived"}:
            raise ValueError("Project status must be active or archived.")
        return value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _origin_tuple(value: str) -> tuple[str, str] | None:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc or parsed.path not in {"", "/"}:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    return parsed.scheme.lower(), parsed.netloc.lower()


def build_auth_router(
    *,
    session_factory: sessionmaker,
    engine,
    UserAccount,
    WorkspaceMembership,
    BrowserSession,
    MagicLinkChallenge,
    AuthRateLimitBucket,
    Workspace,
    Project,
    AuditEvent,
    utcnow,
    token_hash,
    record_audit,
    outbox: list,
    production: bool,
    app_origin: str | None,
    email_sender=None,
) -> APIRouter:
    router = APIRouter()
    cleanup_counter = 0

    def get_session():
        with session_factory() as session:
            yield session

    def check_origin(request: Request) -> None:
        supplied = request.headers.get("origin")
        if not supplied:
            raise HTTPException(status_code=403, detail="A valid request origin is required.")
        expected = app_origin or str(request.base_url).rstrip("/")
        if _origin_tuple(supplied) is None or _origin_tuple(supplied) != _origin_tuple(expected):
            raise HTTPException(status_code=403, detail="Request origin is not allowed.")

    def consume_bucket(session: Session, key: str, limit: int, window_seconds: int) -> bool:
        nonlocal cleanup_counter
        now_epoch = int(utcnow().timestamp())
        window_start = now_epoch - (now_epoch % window_seconds)
        statement_factory = (
            postgresql_insert if engine.dialect.name == "postgresql" else sqlite_insert
        )
        statement = statement_factory(AuthRateLimitBucket).values(
            bucket_key=hashlib.sha256(key.encode("utf-8")).hexdigest(),
            window_start_epoch=window_start,
            request_count=1,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[AuthRateLimitBucket.bucket_key],
            set_={
                "window_start_epoch": window_start,
                "request_count": case(
                    (
                        AuthRateLimitBucket.window_start_epoch == window_start,
                        AuthRateLimitBucket.request_count + 1,
                    ),
                    else_=1,
                ),
            },
        ).returning(AuthRateLimitBucket.request_count)
        count = int(session.scalar(statement) or 0)
        cleanup_counter += 1
        if cleanup_counter >= 1000:
            cleanup_counter = 0
            session.execute(
                AuthRateLimitBucket.__table__.delete().where(
                    AuthRateLimitBucket.window_start_epoch < now_epoch - 86400
                )
            )
        return count <= limit

    def resolve_identity(request: Request, session: Session):
        raw_token = request.cookies.get(SESSION_COOKIE)
        if not raw_token:
            return None
        browser_session = session.scalar(
            select(BrowserSession).where(
                BrowserSession.token_hash == token_hash(raw_token),
                BrowserSession.revoked_at.is_(None),
            )
        )
        now = utcnow()
        if browser_session is None or (_aware(browser_session.expires_at) or now) <= now:
            return None
        user = session.get(UserAccount, browser_session.user_id)
        if user is None or user.verified_at is None:
            return None
        membership = session.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.user_id == user.id,
                WorkspaceMembership.status == "active",
            ).order_by(WorkspaceMembership.created_at.asc())
        )
        if membership is None:
            return None
        workspace = session.get(Workspace, membership.workspace_id)
        if workspace is None or workspace.status != "active":
            return None
        browser_session.last_seen_at = now
        return user, membership, workspace, browser_session

    def project_payload(project):
        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "project_type": project.project_type,
            "status": project.status,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }

    @router.post("/v1/auth/magic-link", status_code=status.HTTP_202_ACCEPTED)
    def request_magic_link(
        payload: MagicLinkRequest,
        request: Request,
        session: Session = Depends(get_session),
    ):
        check_origin(request)
        email = payload.email
        client_host = request.client.host if request.client else "unknown"
        allowed_ip = consume_bucket(session, "magic-ip:" + client_host, 10, 3600)
        allowed_email = consume_bucket(
            session,
            "magic-email:" + hashlib.sha256(email.encode("utf-8")).hexdigest(),
            5,
            3600,
        )
        if not allowed_ip or not allowed_email:
            session.commit()
            return {"message": "If the address can receive a sign-in link, one will be sent."}

        if production and email_sender is None:
            session.rollback()
            raise HTTPException(
                status_code=503,
                detail="Email delivery is not configured.",
            )

        now = utcnow()
        session.query(MagicLinkChallenge).filter(
            MagicLinkChallenge.email == email,
            MagicLinkChallenge.used_at.is_(None),
        ).update({MagicLinkChallenge.used_at: now}, synchronize_session=False)

        raw_token = secrets.token_urlsafe(32)
        challenge = MagicLinkChallenge(
            id=str(uuid.uuid4()),
            email=email,
            token_hash=token_hash(raw_token),
            created_at=now,
            expires_at=now + timedelta(seconds=MAGIC_LINK_SECONDS),
            used_at=None,
        )
        session.add(challenge)
        session.commit()

        base_origin = app_origin or str(request.base_url).rstrip("/")
        link = f"{base_origin}/?auth_token={raw_token}"
        try:
            if email_sender is not None:
                email_sender(email, link)
            else:
                # Development/test-only sink. Never exposed through an HTTP endpoint.
                outbox.append({"email": email, "url": link})
        except Exception:
            # Do not leave a valid challenge behind if delivery fails.
            with session_factory() as cleanup:
                cleanup_challenge = cleanup.get(MagicLinkChallenge, challenge.id)
                if cleanup_challenge is not None and cleanup_challenge.used_at is None:
                    cleanup_challenge.used_at = utcnow()
                    cleanup.commit()
            raise HTTPException(status_code=503, detail="Email delivery is temporarily unavailable.")

        return {"message": "If the address can receive a sign-in link, one will be sent."}

    @router.post("/v1/auth/verify")
    def verify_magic_link(
        payload: MagicLinkVerifyRequest,
        request: Request,
        response: Response,
        session: Session = Depends(get_session),
    ):
        check_origin(request)
        client_host = request.client.host if request.client else "unknown"
        if not consume_bucket(session, "verify-ip:" + client_host, 30, 3600):
            session.commit()
            raise HTTPException(status_code=429, detail="Too many verification attempts. Try again later.")
        session.commit()
        challenge = session.scalar(
            select(MagicLinkChallenge)
            .where(MagicLinkChallenge.token_hash == token_hash(payload.token))
            .with_for_update()
        )
        now = utcnow()
        if (
            challenge is None
            or challenge.used_at is not None
            or (_aware(challenge.expires_at) or now) <= now
        ):
            raise HTTPException(status_code=400, detail="This sign-in link is invalid or expired.")

        challenge.used_at = now
        insert_fn = (
            postgresql_insert if engine.dialect.name == "postgresql" else sqlite_insert
        )
        user_id = str(uuid.uuid4())
        inserted_user_id = session.scalar(
            insert_fn(UserAccount)
            .values(
                id=user_id,
                email=challenge.email,
                verified_at=now,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=[UserAccount.email])
            .returning(UserAccount.id)
        )
        user = (
            session.get(UserAccount, inserted_user_id)
            if inserted_user_id
            else session.scalar(select(UserAccount).where(UserAccount.email == challenge.email).with_for_update())
        )
        if user is None:
            session.rollback()
            raise HTTPException(status_code=409, detail="Unable to establish the account. Retry sign-in.")
        if user.verified_at is None:
            user.verified_at = now

        membership = session.scalar(
            select(WorkspaceMembership).where(
                WorkspaceMembership.user_id == user.id,
                WorkspaceMembership.status == "active",
            ).order_by(WorkspaceMembership.created_at.asc())
        )
        if membership is None:
            workspace = Workspace(
                id="wsp_" + uuid.uuid4().hex,
                name="Personal workspace",
                status="active",
                created_at=now,
            )
            session.add(workspace)
            session.flush()
            membership = WorkspaceMembership(
                id=str(uuid.uuid4()),
                user_id=user.id,
                workspace_id=workspace.id,
                role="owner",
                status="active",
                created_at=now,
            )
            session.add(membership)
        else:
            workspace = session.get(Workspace, membership.workspace_id)
            if workspace is None or workspace.status != "active":
                session.rollback()
                raise HTTPException(status_code=403, detail="The account workspace is unavailable.")

        raw_session = secrets.token_urlsafe(32)
        browser_session = BrowserSession(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=token_hash(raw_session),
            created_at=now,
            expires_at=now + timedelta(seconds=SESSION_SECONDS),
            revoked_at=None,
            last_seen_at=now,
        )
        session.add(browser_session)
        record_audit(
            session,
            event_type="account.signed_in",
            actor_type="user",
            actor_id=user.id,
            target_type="user",
            target_id=user.id,
            workspace_id=workspace.id,
            details={"method": "email_magic_link"},
        )
        session.commit()
        response.set_cookie(
            SESSION_COOKIE,
            raw_session,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=production,
            samesite="lax",
            path="/",
        )
        return {
            "authenticated": True,
            "email": user.email,
            "workspace_name": workspace.name,
        }

    @router.get("/v1/auth/me")
    def current_user(
        request: Request,
        session: Session = Depends(get_session),
    ):
        identity = resolve_identity(request, session)
        if identity is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        user, membership, workspace, browser_session = identity
        session.commit()
        return {
            "authenticated": True,
            "email": user.email,
            "workspace_name": workspace.name,
            "role": membership.role,
        }

    @router.post("/v1/auth/logout")
    def logout(
        request: Request,
        response: Response,
        session: Session = Depends(get_session),
    ):
        check_origin(request)
        raw_token = request.cookies.get(SESSION_COOKIE)
        if raw_token:
            browser_session = session.scalar(
                select(BrowserSession).where(
                    BrowserSession.token_hash == token_hash(raw_token),
                    BrowserSession.revoked_at.is_(None),
                )
            )
            if browser_session is not None:
                browser_session.revoked_at = utcnow()
                record_audit(
                    session,
                    event_type="account.signed_out",
                    actor_type="user",
                    actor_id=browser_session.user_id,
                    target_type="user",
                    target_id=browser_session.user_id,
                )
                session.commit()
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return {"logged_out": True}

    @router.get("/v1/app/projects")
    def list_browser_projects(
        request: Request,
        session: Session = Depends(get_session),
    ):
        identity = resolve_identity(request, session)
        if identity is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        _user, membership, _workspace, _browser_session = identity
        projects = session.scalars(
            select(Project)
            .where(Project.workspace_id == membership.workspace_id)
            .order_by(Project.created_at.desc())
            .limit(100)
        ).all()
        session.commit()
        return {"projects": [project_payload(item) for item in projects]}

    @router.post("/v1/app/projects", status_code=status.HTTP_201_CREATED)
    def create_browser_project(
        payload: BrowserProjectCreate,
        request: Request,
        session: Session = Depends(get_session),
    ):
        check_origin(request)
        identity = resolve_identity(request, session)
        if identity is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        _user, membership, _workspace, _browser_session = identity
        now = utcnow()
        project = Project(
            id="prj_" + uuid.uuid4().hex,
            workspace_id=membership.workspace_id,
            name=payload.name,
            description=payload.description,
            project_type=payload.project_type,
            status="active",
            created_at=now,
            updated_at=now,
        )
        session.add(project)
        record_audit(
            session,
            event_type="project.created",
            actor_type="user",
            actor_id=_user.id,
            target_type="project",
            target_id=project.id,
            workspace_id=membership.workspace_id,
            details={"project_type": project.project_type},
        )
        session.commit()
        return project_payload(project)

    @router.patch("/v1/app/projects/{project_id}")
    def update_browser_project(
        project_id: str,
        payload: BrowserProjectUpdate,
        request: Request,
        session: Session = Depends(get_session),
    ):
        check_origin(request)
        identity = resolve_identity(request, session)
        if identity is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        user, membership, _workspace, _browser_session = identity
        if not payload.model_fields_set:
            raise HTTPException(status_code=422, detail="At least one project field is required.")
        project = session.scalar(
            select(Project).where(
                Project.id == project_id,
                Project.workspace_id == membership.workspace_id,
            )
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        if project.status == "archived" and payload.status != "active":
            raise HTTPException(status_code=409, detail="Archived projects must be reactivated before editing.")
        for field in payload.model_fields_set:
            setattr(project, field, getattr(payload, field))
        project.updated_at = utcnow()
        record_audit(
            session,
            event_type="project.updated",
            actor_type="user",
            actor_id=user.id,
            target_type="project",
            target_id=project.id,
            workspace_id=membership.workspace_id,
            details={"fields": sorted(payload.model_fields_set)},
        )
        session.commit()
        return project_payload(project)

    return router
