# MotionLab v0.2 architecture

## Scope

The first increment proves a constrained Blender execution path. It does not implement the Grey UI, an OYOSKY reasoning engine, a general-purpose backend, rendering, animation generation, or GrowthFlow integration.

## Runtime flow

1. Grey or another authorized client requests work through the MotionLab service.
2. The service authenticates the Blender add-on and offers only approved jobs.
3. The add-on makes outbound HTTPS requests. No inbound laptop port is opened.
4. A network worker validates and queues the received job. It must not access Blender data.
5. A Blender application timer drains at most ten jobs per tick on Blender's main thread.
6. The local protocol and permission checks run again before an allowlisted operation executes.
7. The operation result remains local and pending; it is not sent to the service yet.
8. Blender's `save_post` handler releases results only after a successful `.blend` save has persisted both the scene mutation and project-wide idempotency ledger.
9. The network worker submits the saved result. If the process stops before the service accepts it, the backend lease expires and the job may be redelivered; the persisted ledger returns the original result without repeating the scene mutation.

## First command

Only `create_cube` is implemented. Arbitrary Python, shell commands, file access, scripts, rendering and destructive scene operations are not accepted.

Each job must contain an id, command, args and authorization metadata. The local gate requires `approved: true` and the `scene.write` scope. The service must independently enforce user approval and authorization; client-side checks are defense in depth, not a replacement for server authorization.

## Service contract expected by the connector

- `GET /v1/jobs/next` returns `{"job": null}` when no job is available, or `{"job": { ...validated job... }}`.
- A returned job must be leased to the authenticated add-on installation until its result is accepted or the lease expires. A job not accepted into the local queue must be made available again after lease expiry.
- The service must not dispatch another job to the same installation while an earlier job is leased, unless it explicitly supports bounded concurrent leases.
- `POST /v1/jobs/{job_id}/result` accepts a JSON execution result and is idempotent for a given job id. Repeated submissions must return the original accepted result and must not enqueue or execute the job again.
- The service must persist job state and lease ownership. A lease timeout may cause redelivery, so the Blender operation itself must also be idempotent.
- For `create_cube`, the add-on stores a job ID, SHA-256 fingerprint and original result in a JSON text datablock at project level. The object also carries job ID and fingerprint custom properties as a recovery marker. Re-delivery with the same ID and same payload returns the recorded result without creating another object; reuse of the ID with a different payload is rejected.
- The project-wide ledger survives scene switching and a saved `.blend` file being reopened and prevents replay even if the created object is later deleted.
- The add-on does not submit a successful or failed execution result until Blender's `save_post` callback confirms that the project save completed. Jobs remain queued while the project is untitled. The object marker can recover a missing ledger entry when the object still exists.
- The result acknowledgement is save-gated: a process crash before the `.blend` save leaves the backend job unacknowledged, allowing lease expiry and redelivery. After the save, the project ledger lets a redelivery return the recorded result without repeating the operation. The backend must retain job state and idempotently accept repeated results. Future commands must define and test their own durable idempotency strategy before they can be added to the allowlist.
- The add-on authenticates with an installation-specific bearer credential. The backend stores only its SHA-256 hash and supports revocation.
- Every job is assigned to one installation. Polling and result submission are scoped to the authenticated installation.
- Every lease receives a fresh fencing token. The backend stores only its hash and rejects results from an expired/replaced lease.
- The add-on carries the lease token with the save-gated result. Both endpoints require HTTPS in deployment; the connector rejects non-HTTPS URLs and redirects.
- The admin credential is server-only. A trusted server-side caller must verify user identity, workspace ownership and explicit approval before enqueueing a job.

The v0.2 FastAPI service is implemented in `backend/app.py` and covered by SQLite API tests plus PostgreSQL concurrency integration tests. It is not deployed. Existing databases require a reviewed schema migration before upgrading; `create_all` is not a production migration strategy.

## Threading and safety

- The network worker performs HTTP and queue operations only.
- The local command queue is bounded to 100 pending jobs and never blocks the network worker.
- The outbound result queue is also bounded to 100 entries. When it is full or a result submission fails, the add-on stops accepting further scene work until result delivery recovers.
- Results are held in memory until a successful `.blend` save. Only then are they passed to the outbound result queue.
- HTTPS request timeouts default to two seconds so add-on shutdown has a bounded wait. Shutdown does not attempt unlimited result retries; unacknowledged jobs remain subject to the service lease/recovery policy.
- Blender's main-thread timer processes at most ten jobs per tick to keep the UI responsive under load.
- All `bpy` calls run from Blender's main-thread timer callback.
- TLS certificate verification remains enabled by the standard HTTPS client.
- Only HTTPS service URLs are accepted; authenticated redirects are rejected.
- Responses are size-limited and parsed as JSON.
- Commands are allowlisted and validated before execution.
- Approval and scope are checked again immediately before execution.

## LogicalSky boundaries

MotionLab is a controlled execution environment. It does not duplicate Grey's orchestration or OYOSKY's reasoning. Any future OYOSKY adapter must preserve the existing read-only Grey-to-OYOSKY boundary and request execution through explicit MotionLab authorization.
