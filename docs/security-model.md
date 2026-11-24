# MotionLab v0.3 security model

## Credentials

- The admin credential is a server-side secret. It is not a Blender credential.
- Installation credentials are generated with a cryptographically secure random generator.
- Only a SHA-256 digest of an installation credential is persisted.
- The plaintext installation credential is returned once by the provisioning endpoint.
- Revocation disables authentication immediately. The credential must be re-provisioned rather than recovered.
- The current Blender add-on stores its installation credential in Blender AddonPreferences using a password UI field. This masks the UI but does not encrypt the value at rest. OS-backed secret storage or short-lived credentials remain future hardening work.

## Workspace tenancy and credentials

- A workspace is the tenant boundary. Every installation and job has one non-null workspace owner.
- Workspace credentials are high-entropy service-to-service bearer secrets. Only SHA-256 digests are persisted; credentials can be issued and revoked independently.
- Workspace identity is derived from the authenticated credential. The caller cannot select or override the workspace ID in the job request.
- Workspace credentials can manage only installations and jobs in their workspace. Workspace audit queries are filtered by the authenticated workspace.
- Workspace credentials are not accepted by Blender worker endpoints. Installation credentials are not accepted by workspace endpoints. The global admin credential is not accepted from browser or device clients.
- Deactivating a workspace blocks workspace API access and worker authentication. Existing unfinished jobs remain stored; customer-facing cancellation/recovery policy still needs to be defined before production use.
- The migration adopts all pre-existing installations, jobs, and audit history into an explicit wsp_legacy workspace. It does not discard existing rows.

## Job ownership and leases

- Every job has exactly one installation assignment, and the job and installation must share a workspace.
- An installation can only poll jobs assigned to it in its own workspace and submit results for those jobs.
- Each installation has one lease row, serialized with a PostgreSQL row lock.
- Each lease attempt receives a new random fencing token. Only its hash is stored.
- A result must present the current installation credential and current lease token.
- Redelivery replaces the fencing token. A late result from an earlier attempt is rejected.
- Revocation removes unfinished job assignments and clears the installation's active lease. Those jobs remain queued but require an administrator to assign them to an active installation again.

## Approval boundary

The `approved` field and `scene.write` scope are validated by the API, but the API does not authenticate end users or independently retrieve an approval record. Workspace credentials are a trusted service-to-service boundary, not end-user authentication. A production Grey/orchestrator integration must authenticate the user, verify workspace membership and permissions, obtain explicit approval, and only then call the workspace API. Never expose admin or workspace credentials to a browser, Blender add-on, mobile app, or user device.

## Deployment blockers

Before a public deployment, review and implement:

1. Database migrations for existing databases. SQLAlchemy `create_all` does not alter existing tables.
2. Server-side admin secret storage and rotation.
3. Rate limiting and abuse controls for administrative and worker endpoints.
4. Structured audit events for provisioning, revocation, assignment, enqueue, lease, and result acceptance.
5. Backups, restore testing, monitoring, and alerting.
6. OS-backed or short-lived worker credential storage and rotation.
7. Trusted-orchestrator user authentication, workspace membership/roles, and verifiable approval records.
8. Production HTTPS termination, CORS policy if browser clients are later introduced, and network access restrictions.

This is a workspace-isolation increment, not a complete customer identity or membership system. PostgreSQL CI tests validate tenant polling and row-lock behavior in an ephemeral CI database; they do not validate a hosted production database or deployment.