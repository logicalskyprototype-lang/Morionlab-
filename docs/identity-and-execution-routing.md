# MotionLab identity and execution routing

Status: architecture decision draft. Documentation only; no authentication or execution-provider code is enabled by this document.

## Product decisions

MotionLab has two identity paths and two execution paths. They must remain separate.

| Actor | Identity path | Default execution |
| --- | --- | --- |
| LogicalSky owner | Private Grey-to-MotionLab trusted handoff | Owner-controlled local machine |
| Customer | MotionLab customer authentication, independent of Grey | RunPod customer execution |

Grey's public availability is deferred until LogicalSky Lab is ready. MotionLab customer access must not depend on Grey being public. The owner may use a private Grey integration when the Grey-side issuer contract is ready.

Owner jobs must never incur RunPod charges automatically. If the owner's local machine is offline or unavailable, the job remains queued/paused and MotionLab reports that local execution is unavailable. Any future owner cloud fallback requires a separate explicit opt-in and cost confirmation.

## Trust boundaries

1. The browser is an untrusted public client. It receives only a short-lived MotionLab application session, never a workspace credential, admin token, installation token, RunPod API key, or signing secret.
2. MotionLab's server-side application layer authenticates the human and checks workspace membership/role before using the existing workspace-scoped service credential.
3. The existing workspace credential remains a server-to-server tenant credential. It is not a human identity token.
4. Blender installations retain separate installation credentials and can only poll/acknowledge jobs assigned to that installation.
5. Execution-provider credentials and RunPod account configuration remain server-side.
6. MotionLab independently enforces workspace ownership and authorization. A trusted upstream identity assertion does not replace MotionLab authorization.

## Owner identity: private Grey handoff

The intended flow is:

1. The owner opens MotionLab through a private Grey action.
2. Grey authenticates the owner and checks that the account is entitled to the LogicalSky owner workspace.
3. Grey issues a short-lived, asymmetrically signed identity assertion for MotionLab.
4. The MotionLab server validates the signature against a pinned/rotatable Grey public key and validates issuer, audience, subject, issued-at, expiry, token ID, and intended workspace.
5. MotionLab consumes the assertion's unique token ID once, then creates its own application session.
6. The browser receives only a Secure, HttpOnly, SameSite session cookie. The assertion is not persisted in localStorage or exposed to frontend JavaScript.
7. MotionLab resolves the owner's workspace from server-side identity mapping and checks the requested action's permissions.

Required assertion properties:
- A documented issuer controlled by Grey.
- A MotionLab-specific audience.
- Stable subject mapped to a MotionLab user record.
- Short expiry, target maximum 60 seconds.
- Issued-at time, unique token ID, and an explicit owner/workspace entitlement.
- Asymmetric signing (for example EdDSA or RS256); no shared signing secret in the browser.
- Key ID and a documented key rotation/revocation procedure.
- One-time assertion consumption with a server-side replay store.
- TLS on the handoff and exchange endpoints.

Do not implement a verifier until Grey's actual issuer, signing algorithm, key-discovery/rotation contract, claim names, and private callback flow are documented and testable. Do not invent a token format or accept unsigned/browser-supplied identity fields.

Grey's public launch is not a prerequisite for customer sign-in or customer execution.

## Customer identity: independent of Grey

Customer sign-in is owned by MotionLab and must work before Grey is publicly available.

The customer path must:
- Authenticate a customer through a MotionLab-controlled identity flow.
- Create a server-side session and resolve the customer's workspace membership.
- Enforce workspace role and project/job permissions on every protected operation.
- Support account recovery, session revocation, rate limits, and audit events before public launch.
- Avoid trusting workspace IDs, role names, account IDs, or approval flags supplied by the browser.

The exact customer identity mechanism (for example, email verification/magic link or a supported identity provider) is intentionally not selected in this increment. Choose it after reviewing operational cost, account recovery, abuse controls, and deployment constraints. Do not build a bespoke password system as a shortcut.

## Browser application and BFF

A server-side backend-for-frontend (BFF) is the only browser-facing path to workspace APIs.

- Browser calls same-origin BFF routes using the MotionLab session cookie.
- Session cookies use Secure, HttpOnly, and an appropriate SameSite policy.
- Mutating cookie-authenticated requests require CSRF protection and Origin validation.
- The BFF validates the session, user status, workspace membership, role, and action permission.
- The BFF holds the workspace credential server-side and calls the existing workspace API.
- The BFF never returns the workspace credential to the browser.
- Logout and account/workspace revocation invalidate server-side sessions.
- Apply request size limits, rate limits, structured audit events, and generic authentication errors.
- Configure CORS narrowly; prefer same-origin deployment rather than permissive cross-origin browser access.

The existing workspace projects API already persists workspace-scoped project metadata. The browser shell currently uses sample project data and must be connected through the BFF, not by calling the workspace API directly.

## Execution-provider routing

Introduce a server-side provider interface behind the job orchestration layer. Project metadata and identity must not depend on a specific provider.

Conceptual interface:
- capabilities(): report supported operations and resource constraints.
- submit(job): submit an authorized job to a selected provider.
- get_status(provider_job_id): retrieve provider state.
- cancel(provider_job_id): request cancellation where supported.
- collect_result(provider_job_id): validate and persist a completed result.

Initial provider policies:
- Owner workspace: local provider only.
- Customer workspace: RunPod provider.
- Owner local machine offline: retain queued/paused state; notify the owner; do not select RunPod as a fallback.
- Customer RunPod unavailable: retain a retryable/failed state with bounded retry policy; never pretend a job completed.
- Provider selection is server-side and derived from the authenticated workspace/plan policy, not a browser parameter.
- Persist the selected provider and provider job reference with each job so retries and status checks cannot silently switch providers.
- Require idempotency keys, bounded timeouts, explicit state transitions, and durable audit events.
- Do not expose RunPod API credentials or provider internals to the browser or Blender add-on.

The existing Blender add-on is an outbound worker and can be evolved into the local execution agent. Device enrollment, heartbeat/availability, capability reporting, job routing, and owner-only device authorization need a separate implementation and test plan. Do not treat an open browser tab as proof that the local machine is online.

## Cost and consent controls

- Owner execution is local by default and must not create cloud GPU spend.
- No automatic local-to-RunPod fallback for the owner.
- Customer RunPod usage must be bounded by server-side quotas/budgets and explicit product billing policy before paid execution is enabled.
- RunPod integration must first be developed against a fake provider in tests. No real account setup, GPU rental, paid resource, or billing change is authorized by this design document.
- Provider failures, retries, cancellations, and cost estimates must be observable and auditable.

## Recommended implementation sequence

1. Confirm Grey's real private assertion contract and key-rotation mechanism. Until then, build no Grey verifier.
2. Decide the customer identity mechanism and session lifecycle.
3. Implement the BFF session boundary and authorization tests.
4. Connect the existing project UI to the BFF and persisted project endpoints.
5. Add local-device enrollment, heartbeat, capability checks, and owner-only local routing.
6. Add a fake RunPod provider and provider state-machine tests.
7. Implement the real RunPod adapter only after customer billing, quotas, budgets, and explicit paid-infrastructure approval.
8. Perform security review, migration rehearsal, and staging validation before any deployment.

## Explicit non-goals for this increment

- No Grey public launch.
- No customer dependency on Grey.
- No browser-held workspace or admin credentials.
- No real RunPod account setup, GPU rental, billing, or cloud execution.
- No deployment, hosted database migration, or production secret changes.
- No automatic paid fallback for owner jobs.
- No duplicate reasoning engine inside MotionLab.
