# Workspace authorization design

Status: design baseline for the workspace-authorization implementation.

## Scope

This increment adds tenant boundaries to the existing installation-scoped worker API. It does not introduce end-user login, billing, or a new identity provider. Grey or another trusted server-side orchestrator remains responsible for authenticating the human user and checking their membership before using a workspace credential.

The MotionLab API must independently enforce the workspace boundary after that trusted handoff.

## Identity and ownership model

- A workspace is the tenant boundary.
- A workspace credential is a high-entropy, revocable bearer secret issued once by the administrative control plane. Store only its SHA-256 digest.
- A Blender installation belongs to exactly one workspace. It cannot be moved while it has active or unfinished assigned jobs; reassignment requires an explicit administrative operation and audit event.
- Every job belongs to exactly one workspace and is assigned to an installation in that same workspace.
- Job assignment, polling, leasing, result submission, and audit queries must preserve workspace ownership.
- Workspace credentials must never be accepted by Blender worker endpoints. Installation credentials must never authorize workspace job creation.
- The global admin credential remains server-only and is limited to provisioning, revocation, and controlled support operations.

## API boundary

1. The trusted orchestrator authenticates the end user and checks that user's workspace membership.
2. It calls MotionLab with that workspace's credential to enqueue a job.
3. MotionLab derives the workspace identity from the credential. The request body cannot choose or override the workspace ID.
4. MotionLab validates that the target installation is active and belongs to the authenticated workspace.
5. The installation polls only jobs assigned to it and submits results only for its current lease.
6. Administrative operations remain separate and are auditable.

No browser, mobile app, Grey UI, or Blender add-on receives the global admin credential.

## Required persistence changes

- Add workspace records with stable IDs, names, status, and timestamps.
- Add hashed workspace credentials with creation and revocation timestamps.
- Add a non-null workspace ownership column to installations and jobs.
- Add database constraints and indexes supporting workspace-scoped lookups.
- Add an Alembic migration that preserves existing records. Existing installations and jobs must be adopted into an explicitly created legacy workspace; migration must fail safely if ownership cannot be assigned unambiguously.
- Add audit events for workspace creation, credential issuance/revocation, installation provisioning/assignment, and job enqueue/assignment.

## Required authorization invariants

- A workspace credential for workspace A cannot enqueue work for an installation in workspace B.
- A worker installation in workspace A cannot poll or submit a result for a workspace B job, even if it learns the job ID and lease token.
- Admin reassignment cannot cross workspace boundaries unless an explicit audited transfer operation is designed and approved.
- Revoked workspace credentials stop authorizing new jobs immediately.
- Revoking a workspace prevents new jobs and leases; existing work is handled by an explicit cancellation/requeue policy.
- All ownership decisions are made server-side from authenticated principals and persisted relationships, never from caller-supplied workspace IDs.

## Explicitly out of scope

- Human user authentication, password management, OAuth, and social login.
- Membership/role management and organization invitations.
- Billing, quotas, and usage metering.
- Cross-workspace transfers.
- Public self-service workspace signup.

These require a separate identity and product design. The workspace credential is a service-to-service tenant credential, not a substitute for user login.

## Validation required before merge

- Migration tests for empty databases and existing legacy records.
- Isolation tests with at least two workspaces and multiple installations.
- Negative tests for forged workspace IDs, cross-workspace enqueue, assignment, polling, and result submission.
- Revocation tests for workspace credentials and workspace status.
- PostgreSQL concurrency tests proving that workspace filters remain in effect during simultaneous polling and leasing.
- Existing Blender 3.6 smoke tests and all existing API tests remain green.

## Deployment boundary

This change must not deploy or mutate a hosted database. Before deployment, rehearse the migration against a restored backup and verify the legacy-workspace adoption plan. End-user identity and membership checks must be implemented in the trusted orchestrator before enabling customer traffic.
